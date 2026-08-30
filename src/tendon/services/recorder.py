"""Every run becomes an episode — design decision 1.

There is no flag to enable this, because a recorder that can be switched off will be
switched off the first time it costs something.

Writes LeRobotDataset (parquet, plus mp4 once cameras are wired) alongside a sidecar
table holding what that format does not model: confidence traces, interrupt spans,
operator corrections, curation scores. See ADR 0001.

The constraint that governs this module: recording must not measurably slow the control
loop. `record` therefore only appends to in-memory buffers — LeRobot's own writer batches
and encodes on `save_episode`, and the sidecar is written once per episode rather than
per frame. Nothing here touches the disk at control rate.

## Why the schema is derived rather than declared

`LeRobotDataset.create` needs a feature dict up front and it is expensive to change
afterwards, so it is built from the body's `Capability` at `start`. A five-joint arm and
a seven-joint arm produce different schemas from the same code, which is what makes
design decision 3 hold on the data side too: swapping the body swaps the schema, and no
skill has to know.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tendon.kernel.types import (
    Action,
    Capability,
    EpisodeMeta,
    InterruptContext,
    InterruptResolution,
    Observation,
)

# Where episodes go when nothing says otherwise. A single-host runtime, so a user-level
# directory rather than anything system-wide.
DEFAULT_ROOT = Path.home() / ".tendon" / "episodes"

# LeRobot requires a Hub-shaped identifier even for a dataset that is never pushed.
# Namespaced under the local user so that a later `push_to_hub` is a rename, not a
# restructure.
DEFAULT_REPO_ID = "tendon/local"

# Feature keys. LeRobot's convention is `observation.*` for what the body reported and a
# bare `action` for what was commanded; policies and the wider ecosystem index on exactly
# these names, so they are not ours to rename.
_STATE = "observation.state"
_GRIPPER = "observation.gripper"
_ACTION = "action"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecorderError(RuntimeError):
    """Raised when a recording call arrives out of order.

    Loud on purpose. A recorder that quietly ignores a misordered call produces an
    episode that is short rather than absent, and a short episode looks like data.
    """


def features_for(
    capability: Capability,
    *,
    cameras: tuple[str, ...] = (),
    frame_size: tuple[int, int] = (480, 640),
    use_videos: bool = True,
) -> dict[str, dict]:
    """Build the LeRobotDataset feature schema a body implies.

    Separate from `Recorder` so it can be tested without touching a filesystem, and so
    the kernel-side question "what would this body record?" can be answered before
    anything is opened.

    Args:
        capability: The body's declared capability.
        cameras: Cameras actually being recorded — not `capability.cameras`. A body can
            expose a camera that this run does not render, and LeRobot rejects a frame
            that omits any declared feature. Declaring a camera we will not supply turns
            every `add_frame` into an error, so the schema follows what is recorded
            rather than what exists.
        frame_size: Recorded frame size as (height, width) [px]. Must match what the
            driver renders; a mismatch fails at the first frame.
        use_videos: Whether camera streams are declared as video features. False stores
            frames as images, which is slower to read but simpler to inspect.
    """
    joints = [f"joint_{i}" for i in range(capability.dof)]

    features: dict[str, dict] = {
        # [rad] for revolute joints, [m] for prismatic. The body knows which; the schema
        # cannot express per-joint units, so the unit lives with the driver.
        _STATE: {"dtype": "float32", "shape": (capability.dof,), "names": joints},
        # Commanded joint targets, plus one trailing channel for the gripper when the
        # body has one. Kept in a single `action` feature because that is the key every
        # LeRobot policy trains against.
        _ACTION: {
            "dtype": "float32",
            "shape": (capability.dof + (1 if capability.gripper.value != "none" else 0),),
            "names": joints + (["gripper"] if capability.gripper.value != "none" else []),
        },
    }

    if capability.gripper.value != "none":
        # Normalised 0 closed, 1 open. Recorded separately from `action` because this is
        # what the body reported, not what was asked of it, and the difference is the
        # whole point when a grasp fails.
        features[_GRIPPER] = {"dtype": "float32", "shape": (1,), "names": ["open"]}

    unknown = [c for c in cameras if c not in capability.cameras]
    if unknown:
        raise ValueError(
            f"body {capability.body_id} exposes {list(capability.cameras)}, "
            f"asked to record {unknown}"
        )

    height, width = frame_size
    for camera in cameras:
        features[f"observation.images.{camera}"] = {
            "dtype": "video" if use_videos else "image",
            "shape": (height, width, 3),
            "names": ["height", "width", "channel"],
        }

    return features


class Recorder:
    """Writes one episode at a time to a LeRobotDataset plus a sidecar.

    Attach it to the scheduler's bus with `attach_to`, or drive it directly with `record`.
    The bus path is what makes design decision 1 structural rather than a promise: the
    recorder is a subscriber that is always present, not a mode someone can turn off.
    """

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        repo_id: str = DEFAULT_REPO_ID,
        use_videos: bool = True,
        image_writer_threads_per_camera: int = 4,
    ) -> None:
        """
        Args:
            root: Where episodes are stored. Defaults to `~/.tendon/episodes`.
            repo_id: Hub-shaped dataset identifier. LeRobot requires one even for a
                dataset that is never pushed.
            use_videos: Store camera streams as mp4 rather than as individual images.
            image_writer_threads_per_camera: Threads LeRobot uses to write frames off the
                calling thread. **Not a tuning knob — a correctness one.** Writing frames
                synchronously costs 4.2 ms per step, which does not fit a 10 ms control
                period; at four threads it costs 0.35 ms. Eight measured worse than four,
                so more is not better. Zero writes synchronously and is only useful for
                isolating this effect. See `benchmarks/README.md`.
        """
        self._root = Path(root) if root is not None else DEFAULT_ROOT
        self._repo_id = repo_id
        self._use_videos = use_videos
        self._writer_threads_per_camera = int(image_writer_threads_per_camera)

        # Typed `Any` rather than `Any | None`: whether a dataset is open is tracked by
        # `_episode_id`, checked at the top of every method that touches it. Encoding
        # the same invariant twice would mean two places to keep in agreement.
        self._dataset: Any = None
        self._episode_id: str | None = None
        self._meta: EpisodeMeta | None = None
        self._task: str = ""
        self._cameras: tuple[str, ...] = ()
        # Set by `attach_to`; unused when a caller drives `record` directly.
        self._frames_source: Callable[[], dict[str, Any]] | None = None

        # Sidecar rows for the open episode, flushed on `finish`. Held per episode rather
        # than per frame because writing at control rate is what design decision 1
        # forbids.
        self._sidecar: list[dict[str, Any]] = []
        self._interrupts: list[dict[str, Any]] = []

    # -------------------------------------------------------------- lifecycle

    def start(
        self,
        skill: str,
        capability: Capability,
        *,
        fps: int | None = None,
        cameras: tuple[str, ...] = (),
        frame_size: tuple[int, int] = (480, 640),
    ) -> str:
        """Open an episode and return its id.

        Takes the whole `Capability` rather than a `body_id` because the schema is
        derived from it. A recorder that only knew the body's name would have to look the
        body up, which would make services depend on drivers — the import the boundary
        test forbids.

        Args:
            skill: Skill being run. Recorded as the LeRobot `task` string, which is what
                a language-conditioned policy reads.
            capability: The body's declared capability.
            fps: Frame rate to declare [Hz]. Defaults to the body's control rate, since
                one frame is recorded per control step.
            cameras: Cameras this run records. Must match what is passed to `record`.
            frame_size: Recorded frame size as (height, width) [px].
        """
        if self._episode_id is not None:
            raise RecorderError(
                f"episode {self._episode_id} is still open; call finish() before start()"
            )

        from lerobot.datasets.lerobot_dataset import LeRobotDataset

        rate = int(fps if fps is not None else round(capability.control_hz))
        episode_id = uuid.uuid4().hex[:12]
        root = self._root / self._repo_id.replace("/", "__")

        if root.exists():
            # Append to the existing store. Every run lands in one dataset rather than
            # one dataset per run, because a training set of 300 single-episode datasets
            # is not a training set.
            self._dataset = LeRobotDataset.resume(self._repo_id, root=root)
        else:
            self._dataset = LeRobotDataset.create(
                repo_id=self._repo_id,
                fps=rate,
                features=features_for(
                    capability,
                    cameras=cameras,
                    frame_size=frame_size,
                    use_videos=self._use_videos,
                ),
                root=root,
                robot_type=capability.body_id,
                use_videos=self._use_videos,
                # Threads, not processes. Processes would pay a pickling cost per frame
                # for a 230 KB array, and the work being moved off the caller is I/O and
                # encoding, which releases the GIL anyway.
                image_writer_threads=self._writer_threads_per_camera * len(cameras),
                image_writer_processes=0,
            )

        self._episode_id = episode_id
        self._task = skill
        self._cameras = tuple(cameras)
        self._frame_count = 0
        self._sidecar.clear()
        self._interrupts.clear()
        self._meta = EpisodeMeta(
            episode_id=episode_id,
            skill=skill,
            body_id=capability.body_id,
            started_at=_now(),
        )
        return episode_id

    def record(
        self,
        observation: Observation,
        action: Action,
        *,
        frames: dict[str, Any] | None = None,
        confidence: float | None = None,
        intervention: bool = False,
    ) -> None:
        """Append one timestep. Called at control rate, so it only buffers.

        `confidence` and `intervention` go to the sidecar rather than into the dataset
        itself: an episode stays valid LeRobotDataset for any external consumer, and
        tendon sees the richer view by joining. ADR 0001.

        Args:
            observation: What the body reported.
            action: What the body **applied** — the value `Driver.apply` returned, not the
                one handed to it. The two differ whenever hardware clips, and recording
                the command instead would train a policy on its own requests as though
                they were outcomes.
            frames: Rendered pixels keyed by camera name, from the driver's `render()`.
                Required for exactly the cameras named at `start`, because LeRobot
                rejects a frame missing any declared feature.
            confidence: Policy confidence for this step, if the policy reports one. Most
                do not — see docs/collaboration.md.
            intervention: Whether a human was driving at this step.
        """
        if self._episode_id is None:
            raise RecorderError("no episode is open; call start() first")

        supplied = set(frames or {})
        if supplied != set(self._cameras):
            raise RecorderError(
                f"episode declared cameras {sorted(self._cameras)}, "
                f"this frame supplied {sorted(supplied)}"
            )

        import numpy as np

        frame: dict[str, Any] = {
            _STATE: np.asarray(observation.proprio.joint_positions, dtype=np.float32),
            "task": self._task,
        }

        values = list(action.values)
        if action.gripper is not None:
            values = values + [float(action.gripper)]
        frame[_ACTION] = np.asarray(values, dtype=np.float32)

        if observation.proprio.gripper_open is not None:
            frame[_GRIPPER] = np.asarray([observation.proprio.gripper_open], dtype=np.float32)

        for camera, pixels in (frames or {}).items():
            frame[f"observation.images.{camera}"] = pixels

        self._dataset.add_frame(frame)

        self._sidecar.append(
            {
                "episode_id": self._episode_id,
                "frame_index": self._frame_count,
                "confidence": confidence,
                "intervention": intervention,
                "sim_time_s": observation.extra.get("sim_time_s"),
            }
        )
        self._frame_count += 1

    def attach_to(
        self,
        bus: Any,
        *,
        name: str = "recorder",
        frames: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        """Subscribe to the scheduler's step bus.

        Args:
            bus: The scheduler's `Bus[StepRecord]`.
            name: Subscriber name. Appears in `EpisodeResult.subscriber_failures` if this
                recorder raises, which is how a run that recorded twelve steps and then
                died stops looking like a run that recorded twelve steps.
            frames: Callable returning `{camera: array}`, typically `MujocoDriver.render`.
                Passed here rather than read from the step, because `StepRecord` carries an
                `Observation` and an observation carries frame references rather than
                pixels — `services` cannot import `drivers` to go and fetch them.

        What is recorded is `applied`, not `commanded`. They differ whenever the body
        clipped, and storing the command as though it were the outcome is what the
        `Driver.apply` contract was changed to prevent.
        """
        bus.subscribe(name, self._on_step)
        self._frames_source = frames

    def _on_step(self, record: Any) -> None:
        """Bus callback. Deliberately thin — it runs at control rate.

        Confidence is not recorded here and that is a gap, not a decision. `StepRecord`
        carries no confidence: it is a per-step object while confidence is a property of
        the chunk the step came from. Until the scheduler carries it, the sidecar's
        confidence column is null for bus-driven episodes and populated only when a caller
        drives `record` directly. Noted in docs/collaboration.md.
        """
        self.record(
            record.observation,
            record.applied,
            frames=self._frames_source() if self._frames_source is not None else None,
        )

    def note_interrupt(self, context: InterruptContext, resolution: InterruptResolution) -> None:
        """Record that control was handed to a human, and what they decided.

        The most valuable rows in the store. Demonstration data almost never contains
        recovery from failure, and this is the only place it gets written down.
        """
        if self._episode_id is None:
            raise RecorderError("no episode is open; call start() first")

        self._interrupts.append(
            {
                "episode_id": self._episode_id,
                "frame_index": context.step,
                "reason": context.reason.value,
                "resolution": resolution.resolution.value,
                "note": resolution.note,
                "corrected": resolution.correction is not None,
            }
        )

    def finish(self, success: bool | None = None) -> EpisodeMeta:
        """Close the episode, encode it, and write the sidecar.

        The expensive call in this module, and deliberately the only one. `save_episode`
        encodes video and writes parquet; doing it here rather than per frame is what
        keeps `record` off the disk.
        """
        if self._episode_id is None or self._meta is None:
            raise RecorderError("no episode is open")

        if self._frame_count == 0:
            # Nothing was recorded. Discard rather than write an empty episode: a
            # zero-frame episode is indistinguishable from a broken one downstream.
            self._dataset.clear_episode_buffer()
            episode_index = None
        else:
            self._dataset.save_episode()
            # Read after the write, so it is the index LeRobot actually assigned rather
            # than one predicted before the fact. A discarded episode gets None: it has
            # no row in the parquet to join to.
            episode_index = self._dataset.num_episodes - 1
        self._dataset.finalize()

        self._write_sidecar(episode_index)

        meta = self._meta.model_copy(
            update={
                "ended_at": _now(),
                "steps": self._frame_count,
                "interrupts": len(self._interrupts),
                "success": success,
            }
        )
        self._episode_id = None
        self._meta = None
        self._dataset = None
        return meta

    # --------------------------------------------------------------- sidecar

    @property
    def sidecar_path(self) -> Path:
        """DuckDB file holding what LeRobotDataset does not model.

        Kept beside the dataset rather than inside it so the dataset directory stays
        exactly what an external LeRobot consumer expects.
        """
        return self._root / self._repo_id.replace("/", "__") / "tendon_sidecar.duckdb"

    def _write_sidecar(self, episode_index: int | None = None) -> None:
        """Flush this episode's sidecar rows. One transaction, once per episode.

        Args:
            episode_index: The index LeRobot filed this episode under, or None when it was
                discarded for having no frames.

        `episode_index` is the column that makes this file joinable to the dataset. Without
        it the sidecar keys everything by the recorder's uuid while the parquet numbers
        episodes from zero, and "how many interrupts did episode 7 have" cannot be answered
        from the store at all -- which is why `services/progress.py` keeps its own log
        rather than deriving one.

        Added after the tables already existed in the field, so the ALTER runs for sidecars
        written before this column did. Rows from those runs keep NULL, because the join
        they needed was never recorded and inventing one would be worse than admitting it.
        """
        if not self._sidecar and not self._interrupts:
            return

        import duckdb

        self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(self.sidecar_path))
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS frames (
                    episode_id    VARCHAR,
                    episode_index BIGINT,
                    frame_index  BIGINT,
                    confidence   DOUBLE,
                    intervention BOOLEAN,
                    sim_time_s   DOUBLE
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS interrupts (
                    episode_id    VARCHAR,
                    episode_index BIGINT,
                    frame_index BIGINT,
                    reason      VARCHAR,
                    resolution  VARCHAR,
                    note        VARCHAR,
                    corrected   BOOLEAN
                )
                """
            )
            # Named columns rather than positional: a positional INSERT silently shifts
            # every value one place the first time a column is added, which is exactly
            # what adding this one would have done.
            for table in ("frames", "interrupts"):
                con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS episode_index BIGINT")

            if self._sidecar:
                con.executemany(
                    "INSERT INTO frames "
                    "(episode_id, episode_index, frame_index, confidence, intervention, "
                    "sim_time_s) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            r["episode_id"],
                            episode_index,
                            r["frame_index"],
                            r["confidence"],
                            r["intervention"],
                            r["sim_time_s"],
                        )
                        for r in self._sidecar
                    ],
                )
            if self._interrupts:
                con.executemany(
                    "INSERT INTO interrupts "
                    "(episode_id, episode_index, frame_index, reason, resolution, note, "
                    "corrected) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            r["episode_id"],
                            episode_index,
                            r["frame_index"],
                            r["reason"],
                            r["resolution"],
                            r["note"],
                            r["corrected"],
                        )
                        for r in self._interrupts
                    ],
                )
        finally:
            con.close()
        self._sidecar.clear()
        self._interrupts.clear()
