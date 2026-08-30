"""Recorded episodes as a body — the part of design decision 3 that is ours.

LeRobot abstracts *robots*. This driver abstracts something that is not a robot at all:
a recording. A human demonstration, a teleoperated session, an episode pulled from the
Hub — each becomes a body that produces observations and refuses commands.

That is the whole argument for calling the abstraction an *embodiment* HAL rather than a
robot HAL. If a recording is a body, then human demonstrations and robot episodes land in
one dataset and one code path, instead of the two pipelines that never meet which the
field currently has. If it is not, `drivers/` is just a robot wrapper with extra steps.

What it is useful for, concretely:

- replaying what tendon itself recorded, which is how the recorder gets verified
- running a curator or an evaluator over episodes with no simulator attached
- pulling a public dataset off the Hub and stepping through it with tendon's own types

Requires the robot extra:  pip install "tendon-os[robot]"
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tendon.drivers.base import Driver, DriverError, ReadOnlyBody, register
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Capability,
    GripperKind,
    Observation,
    Proprioception,
)

# LeRobot's feature keys, mirrored from services/recorder.py. Duplicated rather than
# imported: `drivers` importing `services` is the dependency the boundary test forbids,
# and these names belong to the LeRobot format rather than to either module.
_STATE = "observation.state"
_GRIPPER = "observation.gripper"
_ACTION = "action"
_IMAGE_PREFIX = "observation.images."


def _now() -> datetime:
    return datetime.now(timezone.utc)


@register("human")
class HumanDriver(Driver):
    """A recorded LeRobotDataset episode, presented as a read-only body.

    Playback position advances through `advance()` rather than through `observe()`.
    `observe()` stays idempotent because the `Driver` contract says it reports the current
    observation, and a method that silently moves a cursor every time it is called cannot
    be used twice in one control step without changing what it returns.

    `advance()` is outside the protocol, like `MujocoDriver.render()`. The protocol
    advances a body through `apply`, which a read-only body must refuse; something has to
    move time forward, and inventing a fake action to do it would put a command that
    nobody issued into the record.
    """

    def __init__(
        self,
        repo_id: str,
        *,
        root: str | Path | None = None,
        episode: int = 0,
    ) -> None:
        """
        Args:
            repo_id: Dataset identifier, local or on the Hub.
            root: Local dataset directory. None resolves through LeRobot's own cache,
                downloading from the Hub if needed.
            episode: Which episode to replay, by index within the dataset.
        """
        try:
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
        except ImportError as exc:  # pragma: no cover - depends on the robot extra
            raise DriverError(
                "LeRobot is not installed. Install the robot extra: "
                'pip install "tendon-os[robot]". Note that reading a dataset needs '
                "lerobot's own `dataset` extra, which pins av>=15,<16."
            ) from exc

        self._repo_id = repo_id
        try:
            self._dataset: Any = LeRobotDataset(repo_id, root=Path(root) if root else None)
        except Exception as exc:
            raise DriverError(f"could not open dataset {repo_id!r}: {exc}") from exc

        self._features = dict(self._dataset.features)
        self._closed = False

        if _STATE not in self._features:
            raise DriverError(
                f"dataset {repo_id!r} has no {_STATE!r} feature, so it does not describe "
                f"a body this driver can present"
            )

        self._cameras = tuple(
            sorted(k[len(_IMAGE_PREFIX) :] for k in self._features if k.startswith(_IMAGE_PREFIX))
        )
        self._dof = int(self._features[_STATE]["shape"][0])
        self._has_gripper = _GRIPPER in self._features

        self._select_episode(episode)

    # ------------------------------------------------------------------- setup

    def _select_episode(self, episode: int) -> None:
        """Resolve an episode index to a half-open range of global frame indices.

        LeRobotDataset indexes frames globally across every episode, so replaying one
        episode means knowing where it starts and stops. Resolved once here rather than
        filtered per frame, since `observe` runs at the control rate.
        """
        total = int(self._dataset.num_episodes)
        if not 0 <= episode < total:
            raise DriverError(
                f"dataset {self._repo_id!r} has {total} episodes, asked for {episode}"
            )

        # Walk the frames of this episode. `episode_index` is a per-frame column, which is
        # the one representation every LeRobotDataset version exposes; the helper indices
        # around it have moved between releases.
        start = stop = None
        for i in range(int(self._dataset.num_frames)):
            index = int(self._dataset.get_raw_item(i)["episode_index"])
            if index == episode and start is None:
                start = i
            elif index != episode and start is not None:
                stop = i
                break
        if start is None:
            raise DriverError(f"episode {episode} has no frames in {self._repo_id!r}")

        self._episode = episode
        self._start = start
        self._stop = stop if stop is not None else int(self._dataset.num_frames)
        self._cursor = start

    # ---------------------------------------------------------------- contract

    @property
    def capability(self) -> Capability:
        """Inferred from the dataset schema, because a recording cannot be asked.

        One thing genuinely cannot be recovered: `GripperKind`. The format records that a
        gripper channel exists and its value, not whether the hardware was a parallel jaw,
        a suction cup or a hand. PARALLEL is reported as the most common case, and a skill
        that needs to distinguish them cannot rely on a replayed body to tell it apart.
        """
        return Capability(
            body_id=f"human:{self._repo_id}#{self._episode}",
            dof=self._dof,
            gripper=GripperKind.PARALLEL if self._has_gripper else GripperKind.NONE,
            control_hz=float(self._dataset.fps),
            cameras=self._cameras,
            has_force_sensing=False,
            # The field this driver exists to set. Everything else is inference; this is
            # the claim.
            readonly=True,
        )

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        """Nothing. A recording executes no action space.

        Empty rather than "whatever it was recorded with": `negotiate` walks this tuple to
        pick a shared space, and returning one here would let a skill load against a body
        that cannot move, failing at the first `apply` instead of at load time.
        """
        return ()

    def reset(self, *, seed: int | None = None) -> Observation:
        """Rewind to the first frame of the episode and report it.

        `seed` is meaningless for a recording and is accepted only to satisfy the
        protocol. Playback is identical every time, which is precisely why this body is
        useful for testing anything else.
        """
        self._require_open()
        self._cursor = self._start
        return self.observe()

    def observe(self) -> Observation:
        """The frame at the current playback position. Idempotent."""
        self._require_open()
        row = self._dataset.get_raw_item(self._cursor)

        state = np.asarray(row[_STATE], dtype=np.float32).reshape(-1)
        gripper_open = None
        if self._has_gripper:
            gripper_open = float(np.asarray(row[_GRIPPER]).reshape(-1)[0])
            gripper_open = float(np.clip(gripper_open, 0.0, 1.0))

        return Observation(
            step=self._cursor - self._start,
            t=_now(),
            proprio=Proprioception(
                joint_positions=[float(v) for v in state],  # [rad]
                # Not recorded by the format. Reported absent rather than differentiated
                # from positions, which would invent a measurement.
                joint_velocities=None,
                gripper_open=gripper_open,
                force=None,
            ),
            frames={
                c: f"{self._repo_id}#{self._episode}/{c}/{self._cursor}" for c in self._cameras
            },
            extra={
                "episode_index": self._episode,
                "frame_index": self._cursor - self._start,
                "timestamp_s": float(np.asarray(row["timestamp"]).reshape(-1)[0]),
            },
        )

    def apply(self, action: Action) -> None:
        """Always refuses. This is a recording; nothing here can be commanded."""
        self._require_open()
        raise ReadOnlyBody(
            f"{self._repo_id!r} is a recording and accepts no commands. "
            f"Use advance() to move playback forward."
        )

    def close(self) -> None:
        """Drop the dataset handle. Safe to call twice, as the protocol requires."""
        if self._closed:
            return
        self._closed = True
        self._dataset = None

    # -------------------------------------------------------------- extensions

    def advance(self) -> bool:
        """Move playback on by one frame. Returns False at the end of the episode.

        Outside the `Driver` protocol on purpose — see the class docstring. A caller that
        ignores the return value replays the final frame forever rather than stopping,
        which is why it returns a value instead of raising: reaching the end of a
        recording is the expected outcome, not a fault.
        """
        self._require_open()
        if self._cursor + 1 >= self._stop:
            return False
        self._cursor += 1
        return True

    def recorded_action(self) -> Action:
        """What was commanded at the current frame.

        The reason a recording is worth presenting as a body at all. An observation alone
        is a video; an observation paired with the action taken is a demonstration, which
        is what imitation learning trains on.
        """
        self._require_open()
        values = np.asarray(self._dataset.get_raw_item(self._cursor)[_ACTION], dtype=np.float32)
        values = values.reshape(-1)

        gripper = None
        if self._has_gripper and values.size == self._dof + 1:
            gripper = float(np.clip(values[-1], 0.0, 1.0))
            values = values[: self._dof]

        return Action(
            # Asserted, not recorded: LeRobotDataset stores action values without saying
            # which space they are in. Joint position is what every body tendon currently
            # writes, and a dataset recorded in another space would be silently
            # mislabelled here. Worth revisiting when a second space is actually written.
            space=ActionSpace.JOINT_POSITION,
            values=[float(v) for v in values],
            gripper=gripper,
        )

    def render(self) -> dict[str, np.ndarray]:
        """Recorded frames at the current position, as uint8 HWC arrays.

        Mirrors `MujocoDriver.render()` so a consumer does not branch on body type.
        LeRobot hands back float CHW tensors normalised to [0, 1]; converting here means
        a recorded body and a simulated body produce the same thing.
        """
        self._require_open()
        if not self._cameras:
            return {}

        item = self._dataset[self._cursor]
        frames: dict[str, np.ndarray] = {}
        for camera in self._cameras:
            pixels = np.asarray(item[f"{_IMAGE_PREFIX}{camera}"])
            if pixels.ndim == 3 and pixels.shape[0] in (1, 3, 4):
                pixels = np.transpose(pixels, (1, 2, 0))  # CHW -> HWC
            if pixels.dtype != np.uint8:
                pixels = (np.clip(pixels, 0.0, 1.0) * 255).astype(np.uint8)
            frames[camera] = pixels
        return frames

    # --------------------------------------------------------------- internals

    def _require_open(self) -> None:
        if self._closed:
            raise DriverError("driver is closed")
