"""MuJoCo driver — the only body v0.1 needs.

Chosen as the default because it installs with pip on any machine, runs without an
NVIDIA GPU, and has the contact physics the manipulation literature trusts. Every CLI
command and every example must work against this driver with no hardware attached, so
that a contributor can run the whole project on a laptop. See docs/stack.md.

The body it loads is an SO-ARM100: five arm joints and a parallel jaw. That is design
decision 3 taken seriously — simulating the arm we intend to build for makes the v0.4
hardware step a driver swap rather than a rewrite. Provenance and licence for the model
are in third_party/mujoco_menagerie/PROVENANCE.md.

Requires the sim extra:  pip install "tendon-os[sim]"
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tendon.drivers.base import Driver, DriverError, register
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Capability,
    GripperKind,
    Observation,
    Proprioception,
)

# The scene shipped with tendon. Resolved relative to this file so that a source checkout
# needs no configuration. An installed wheel additionally needs the assets packaged,
# which is recorded in docs/collaboration.md as a request to Track B.
_DEFAULT_SCENE = Path(__file__).resolve().parent.parent / "assets" / "scenes" / "so_arm100_cube.xml"

# Reset target, looked up by NAME and never by index. The vendored model contributes its
# own "home" and "rest" keyframes at indices 0 and 1, both written for a 6-value qpos.
# Adding the cube's freejoint makes nq 13; MuJoCo pads the missing values with zeros, so
# the cube lands at the world origin, inside the arm's own base. Resetting by index 0
# would start every episode in penetration.
_RESET_KEYFRAME = "start"

# The actuator that closes the gripper rather than moving an arm joint. Held separately
# because `Action` models the gripper as its own scalar in [0, 1] instead of one more
# joint value: a suction cup and a five-finger hand have no comparable joint, but both
# have a meaningful "how closed".
_GRIPPER_ACTUATOR = "Jaw"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    """Run a block with the process working directory moved to `path`.

    Used for exactly one call — loading the MJCF — and for a specific reason.

    MuJoCo's XML parser opens files through a byte-oriented path API. On Windows that
    encodes a non-ASCII absolute path in the active code page, so a checkout under a
    user directory with, say, Korean characters fails at load with
    `ParseXML: Error opening file`, naming a path full of replacement characters. The
    file is present and readable; only MuJoCo cannot open it.

    Neither obvious workaround holds. An 8.3 short path (`GetShortPathNameW`) only
    shortens components longer than eight characters, so a short non-ASCII directory
    name survives intact and still fails. Copying the tree to a temporary ASCII location
    would have to bring the meshes and the vendored model with it on every load.

    Moving the working directory to the scene's own folder makes every path MuJoCo sees
    relative and ASCII: the scene filename, the `../robots/` include, and the
    `../../../third_party/` mesh directory are all ASCII, whatever the checkout is
    called.

    The cost is a process-global mutation, so it is held for the shortest possible span —
    one model load in `__init__`, never during stepping — and always restored. A driver
    constructed on one thread while another thread depends on the working directory would
    still race; MjModel is not shareable across threads anyway, so construction belongs on
    the thread that will run the episode.
    """
    prior = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prior)


@register("mujoco")
class MujocoDriver(Driver):
    """A MuJoCo model exposed as a tendon body.

    One instance owns one MjModel and one MjData and is not safe to share across threads,
    which is why the scheduler holds exactly one driver for the length of an episode.
    """

    def __init__(
        self,
        scene_path: str | Path | None = None,
        *,
        control_hz: float = 100.0,
        gripper_actuator: str = _GRIPPER_ACTUATOR,
        render_cameras: tuple[str, ...] = (),
        render_size: tuple[int, int] = (480, 640),
        render_hz: float = 0.0,
    ) -> None:
        """
        Args:
            scene_path: MJCF scene to load. Defaults to the cube-pick scene shipped with
                tendon.
            control_hz: Rate this body accepts setpoints at [Hz]. Each `apply` advances
                physics by 1/control_hz [s], however many solver substeps that takes.
            gripper_actuator: Name of the actuator that closes the gripper. Excluded from
                the arm's joint vector and surfaced through `Action.gripper` instead.
            render_cameras: Cameras to render when `render()` is called. Empty by
                default: rendering costs milliseconds per frame per camera, and a run
                that records no video should not pay for it. Naming a camera the scene
                does not define is refused here rather than at the first frame.
            render_size: Rendered frame size as (height, width) [px]. Must fit the
                scene's `offwidth`/`offheight`, which bound MuJoCo's offscreen buffer.
            render_hz: Rate a background thread renders at [Hz]. Zero renders inline on
                every `render()` call, which is simple and does not fit a control period —
                measured at 22 ms against a 10 ms budget, see `benchmarks/README.md`. A
                positive value models what a camera actually is: something that produces
                frames on its own clock while the control loop reads whatever is current.
                30 is the rate of most robot cameras.
        """
        try:
            import mujoco
        except ImportError as exc:  # pragma: no cover - depends on the sim extra
            raise DriverError(
                'MuJoCo is not installed. Install the sim extra: pip install "tendon-os[sim]"'
            ) from exc

        self._mj = mujoco
        self._scene_path = Path(scene_path) if scene_path else _DEFAULT_SCENE
        if not self._scene_path.is_file():
            raise DriverError(f"scene not found: {self._scene_path}")

        self._control_hz = float(control_hz)
        if self._control_hz <= 0:
            raise DriverError(f"control_hz must be positive, got {control_hz}")

        # Loaded from inside the scene's own directory; see `_working_directory`.
        with _working_directory(self._scene_path.parent):
            self._model = mujoco.MjModel.from_xml_path(self._scene_path.name)
        self._data = mujoco.MjData(self._model)
        self._closed = False
        self._step = 0

        # Substeps per control step. A control period shorter than one physics timestep
        # would advance no simulation time at all, so it is refused here rather than
        # silently rounded down to zero.
        period_s = 1.0 / self._control_hz
        self._substeps = int(round(period_s / self._model.opt.timestep))
        if self._substeps < 1:
            raise DriverError(
                f"control_hz={self._control_hz} is faster than the scene physics "
                f"timestep {self._model.opt.timestep}s: one control step would advance "
                f"no simulation time"
            )

        self._index_actuators(gripper_actuator)

        self._cameras = tuple(
            mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_CAMERA, i)
            for i in range(self._model.ncam)
        )

        unknown = [c for c in render_cameras if c not in self._cameras]
        if unknown:
            raise DriverError(
                f"scene defines cameras {list(self._cameras)}, cannot render {unknown}"
            )
        self._render_cameras = tuple(render_cameras)
        self._render_size = render_size
        self._render_hz = float(render_hz)
        if self._render_hz < 0:
            raise DriverError(f"render_hz cannot be negative, got {render_hz}")
        # Built on first use. A Renderer allocates an offscreen GL context, which is
        # wasted on a run that never renders — and on some headless machines, fails.
        # Deferring it means those runs work rather than dying at construction.
        self._renderer: Any = None

        # Asynchronous rendering state. All unused when `render_hz` is zero.
        self._render_thread: threading.Thread | None = None
        self._render_stop = threading.Event()
        self._render_ready = threading.Event()
        self._state_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._state_snapshot: tuple[np.ndarray, np.ndarray, float] | None = None
        self._latest_frames: dict[str, np.ndarray] = {}
        self._frames_rendered = 0

        self._reset_key = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_KEY, _RESET_KEYFRAME)
        if self._reset_key < 0:
            raise DriverError(
                f"scene {self._scene_path.name} defines no keyframe named "
                f"{_RESET_KEYFRAME!r}. Resetting by index is unsafe for this body: the "
                f"vendored model contributes keyframes that predate the task objects."
            )

        if self._render_hz > 0 and self._render_cameras:
            self._publish_state()
            self._start_render_thread()

    # ------------------------------------------------------- asynchronous render

    def _publish_state(self) -> None:
        """Hand the render thread a copy of the physics state.

        Called once per control step. The cost is copying `nq` + `nv` floats — 26 numbers
        for this body — under a lock held for the length of that copy, which is why it
        does not show up against a 10 ms budget.

        The copy is the point. `MjData` is not safe to read while `mj_step` writes it, and
        a renderer reading it directly would produce torn frames under contact, where
        every value is changing fastest.
        """
        snapshot = (self._data.qpos.copy(), self._data.qvel.copy(), float(self._data.time))
        with self._state_lock:
            self._state_snapshot = snapshot

    def _start_render_thread(self) -> None:
        """Start the camera thread and wait for its first frame.

        Waiting here rather than letting the caller discover an empty dict later: a
        recorder that declared a camera rejects a frame without it, so "no frame yet" would
        surface as a schema error several steps into an episode.
        """
        self._render_thread = threading.Thread(
            target=self._render_loop, name="tendon-render", daemon=True
        )
        self._render_thread.start()
        if not self._render_ready.wait(timeout=30.0):
            self._render_stop.set()
            raise DriverError(
                "render thread produced no frame within 30s; the offscreen GL context "
                "probably could not be created on this machine"
            )

    def _render_loop(self) -> None:
        """Render at `render_hz` from the last published state, on its own clock.

        Owns its own `MjData` and `Renderer`, and touches neither of the driver's. A GL
        context belongs to the thread that created it, so the renderer cannot be built
        outside this function; and a second `MjData` is what lets this run while the
        control thread steps physics.

        `mj_forward` recomputes the derived quantities a render needs — body poses, camera
        transforms — from the copied `qpos` and `qvel`. It is a fraction of a `mj_step`
        because it integrates nothing.
        """
        height, width = self._render_size
        data = self._mj.MjData(self._model)
        renderer = self._mj.Renderer(self._model, height=height, width=width)
        period_s = 1.0 / self._render_hz
        try:
            while not self._render_stop.is_set():
                started = time.perf_counter()

                with self._state_lock:
                    snapshot = self._state_snapshot
                if snapshot is not None:
                    qpos, qvel, sim_time = snapshot
                    data.qpos[:] = qpos
                    data.qvel[:] = qvel
                    data.time = sim_time
                    self._mj.mj_forward(self._model, data)

                    frames = {}
                    for camera in self._render_cameras:
                        renderer.update_scene(data, camera=camera)
                        frames[camera] = renderer.render()
                    with self._frame_lock:
                        self._latest_frames = frames
                        self._frames_rendered += 1
                    self._render_ready.set()

                # Sleep the remainder of the period, waking early if asked to stop. When a
                # render takes longer than the period the wait is zero and frames simply
                # arrive slower, which is what a real camera under load also does.
                self._render_stop.wait(max(0.0, period_s - (time.perf_counter() - started)))
        finally:
            renderer.close()

    # ------------------------------------------------------------------- setup

    def _index_actuators(self, gripper_actuator: str) -> None:
        """Split actuators into arm joints and the gripper, once, at load time.

        Resolved here rather than per step because the mapping cannot change during a
        session and `observe` runs at the control rate.
        """
        mj = self._mj
        names = [
            mj.mj_id2name(self._model, mj.mjtObj.mjOBJ_ACTUATOR, i) for i in range(self._model.nu)
        ]
        self._arm_actuators = [i for i, name in enumerate(names) if name != gripper_actuator]
        found = [i for i, name in enumerate(names) if name == gripper_actuator]
        self._gripper_actuator: int | None = found[0] if found else None

        if not self._arm_actuators:
            raise DriverError(f"scene has no arm actuators; found only {names}")

        # Addresses for reading state back. actuator_trnid[i, 0] is the joint an actuator
        # drives; qposadr and dofadr are where that joint's position and velocity live.
        self._arm_qpos = [
            int(self._model.jnt_qposadr[self._model.actuator_trnid[i, 0]])
            for i in self._arm_actuators
        ]
        self._arm_dof = [
            int(self._model.jnt_dofadr[self._model.actuator_trnid[i, 0]])
            for i in self._arm_actuators
        ]

        if self._gripper_actuator is not None:
            joint = self._model.actuator_trnid[self._gripper_actuator, 0]
            self._gripper_qpos = int(self._model.jnt_qposadr[joint])
            low, high = self._model.actuator_ctrlrange[self._gripper_actuator]
            self._gripper_range = (float(low), float(high))  # [rad]
        else:
            self._gripper_qpos = -1
            self._gripper_range = (0.0, 1.0)

    # ---------------------------------------------------------------- contract

    @property
    def capability(self) -> Capability:
        """Reported from the loaded model rather than from a constant.

        `dof` counts arm joints only. The gripper is excluded because `Action` carries it
        as its own scalar; counting it here would let a skill that needs six arm axes
        match a five-joint arm that happens to have a jaw.
        """
        return Capability(
            body_id=f"mujoco:{self._scene_path.stem}",
            dof=len(self._arm_actuators),
            gripper=(
                GripperKind.PARALLEL if self._gripper_actuator is not None else GripperKind.NONE
            ),
            control_hz=self._control_hz,
            cameras=self._cameras,
            # MuJoCo can compute contact forces, but this model carries no force sensor.
            # Reporting True would promise a calibrated reading the body does not have.
            has_force_sensing=False,
            readonly=False,
        )

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        """Position control only.

        The vendored model actuates through `<position>` elements, so a velocity setpoint
        has nothing to drive. Advertising JOINT_VELOCITY would make `negotiate` succeed at
        load time and produce wrong motion at run time — exactly the failure the load-time
        check exists to prevent.
        """
        return (ActionSpace.JOINT_POSITION,)

    def reset(self, *, seed: int | None = None) -> Observation:
        """Return to the scene start keyframe and report the resulting state.

        `seed` is accepted and currently unused: the v0.1 scene is deterministic. Domain
        randomisation belongs here when it arrives, and taking the argument now means
        adding it will not change the HAL.
        """
        self._require_open()
        self._mj.mj_resetDataKeyframe(self._model, self._data, self._reset_key)
        self._mj.mj_forward(self._model, self._data)
        self._step = 0
        if self._render_thread is not None:
            self._publish_state()
        return self.observe()

    def observe(self) -> Observation:
        """Current observation. Called at the control rate, so it allocates sparingly."""
        self._require_open()
        qpos = self._data.qpos
        qvel = self._data.qvel

        gripper_open: float | None = None
        if self._gripper_actuator is not None:
            low, high = self._gripper_range
            span = high - low
            raw = float(qpos[self._gripper_qpos])  # [rad]
            # Clipped because the joint can overshoot its actuator range under contact,
            # while `Proprioception.gripper_open` is constrained to [0, 1] by the model.
            gripper_open = float(np.clip((raw - low) / span, 0.0, 1.0)) if span else 0.0

        return Observation(
            step=self._step,
            t=_now(),
            proprio=Proprioception(
                joint_positions=[float(qpos[a]) for a in self._arm_qpos],  # [rad]
                joint_velocities=[float(qvel[a]) for a in self._arm_dof],  # [rad/s]
                gripper_open=gripper_open,
                force=None,
            ),
            # Frames are references, not pixels (see kernel/types.py). Rendering is a
            # separate concern so that a run recording no video pays nothing for cameras.
            frames={name: f"sim://{name}/{self._step}" for name in self._cameras},
            extra={"sim_time_s": float(self._data.time)},
        )

    def apply(self, action: Action) -> Action:
        """Command one control step, and report what the body actually executed.

        Setpoints are written to `ctrl` and physics advances by one control period.

        Values are clipped to each actuator's range and the clipped values are what comes
        back. That is a hardware bound rather than a policy decision: MuJoCo clips
        internally regardless, so the only question is whether the record says what was
        asked or what was done. It says what was done.
        """
        self._require_open()
        if action.space is not ActionSpace.JOINT_POSITION:
            raise DriverError(
                f"this body accepts {ActionSpace.JOINT_POSITION.value}, got {action.space.value}"
            )
        if len(action.values) != len(self._arm_actuators):
            raise DriverError(
                f"expected {len(self._arm_actuators)} joint values, got {len(action.values)}"
            )

        ranges = self._model.actuator_ctrlrange
        applied: list[float] = []
        for slot, value in zip(self._arm_actuators, action.values, strict=True):
            low, high = ranges[slot]
            clipped = float(np.clip(value, low, high))  # [rad]
            self._data.ctrl[slot] = clipped
            applied.append(clipped)

        applied_gripper: float | None = None
        if action.gripper is not None and self._gripper_actuator is not None:
            # Reported back in the same normalised [0, 1] the caller used, not in the
            # joint's radians. A round trip through `Action` has to produce something that
            # can be commanded again.
            applied_gripper = float(np.clip(action.gripper, 0.0, 1.0))
            low, high = self._gripper_range
            self._data.ctrl[self._gripper_actuator] = low + applied_gripper * (high - low)

        for _ in range(self._substeps):
            self._mj.mj_step(self._model, self._data)
        self._step += 1

        if self._render_thread is not None:
            self._publish_state()

        return Action(space=action.space, values=applied, gripper=applied_gripper)

    def render(self) -> dict[str, np.ndarray]:
        """Rendered frames for the cameras this driver was told to render.

        Not part of the `Driver` protocol, and deliberately separate from `observe`.
        `Observation.frames` carries references rather than pixels because an observation
        crosses process and network boundaries many times per second (see
        `kernel/types.py`); the pixels themselves go straight to whatever writes video.

        Returns `{camera_name: uint8 array of shape (height, width, 3)}`. Empty when no
        cameras were requested, which is the default.

        With `render_hz` set this returns the most recent frame the camera thread produced
        and does not block. That means consecutive control steps can see the same frame,
        which is correct rather than a compromise: a 30 fps camera against a 100 Hz loop
        genuinely has no new image for two steps out of three, and pretending otherwise is
        what makes a policy trained in simulation fail on hardware.
        """
        self._require_open()
        if not self._render_cameras:
            return {}

        if self._render_thread is not None:
            with self._frame_lock:
                return dict(self._latest_frames)

        if self._renderer is None:
            height, width = self._render_size
            self._renderer = self._mj.Renderer(self._model, height=height, width=width)

        frames: dict[str, np.ndarray] = {}
        for camera in self._render_cameras:
            self._renderer.update_scene(self._data, camera=camera)
            frames[camera] = self._renderer.render()
        return frames

    def body_position(self, name: str) -> np.ndarray:
        """World position of a named body in the scene [m].

        Outside the `Driver` protocol, and for evaluation rather than for control. A skill
        declares success as a condition on the world — `cube_height_above: 0.1` in
        `skills/grasp/cube-sim/skill.yaml` — and something has to be able to read that.

        A policy must not call this. Ground-truth object positions are available in
        simulation and not on hardware, so a policy that used them would work in MuJoCo
        and fail on an SO-101 in a way no simulation test could catch. `Observation` is
        what a policy sees; this is what a judge sees.
        """
        self._require_open()
        try:
            return np.asarray(self._data.body(name).xpos, dtype=float).copy()
        except KeyError as exc:
            raise DriverError(f"scene has no body named {name!r}") from exc

    @property
    def frames_rendered(self) -> int:
        """How many distinct frames the camera thread has produced.

        Exposed because "how many of my recorded frames are actually different images?"
        is otherwise unanswerable, and the answer is frequently not what a caller expects.

        The camera runs on wall-clock time, as a real one does, while simulation time runs
        as fast as the machine allows. Stepping this body flat out is roughly sixty times
        real time, so a 30 Hz camera yields one new image per two thousand control steps —
        an episode recorded that way holds a nearly static video against a moving arm.

        Two ways out, both the caller's to choose. Pace the control loop toward real time
        while recording, which is what a robot does anyway. Or accept that camera-bearing
        collection is render-bound: at ~22 ms per frame, 30 frames of simulated second cost
        0.67 s, so it runs at best around 1.5x real time no matter how fast the physics is.
        Comparing this count against the step count says which regime a run was in.
        """
        with self._frame_lock:
            return self._frames_rendered

    def close(self) -> None:
        """Stop the camera thread and release the model, data and GL context.

        The thread is joined rather than left to a daemon flag. It holds a GL context and
        an `MjData` built from a model this method is about to drop; letting it run one
        more iteration against freed memory is the kind of crash that gets blamed on the
        simulator. Safe to call twice, as the protocol requires.
        """
        if self._closed:
            return
        self._closed = True

        if self._render_thread is not None:
            self._render_stop.set()
            self._render_thread.join(timeout=5.0)
            self._render_thread = None

        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
        self._data = None
        self._model = None

    # --------------------------------------------------------------- internals

    def _require_open(self) -> None:
        if self._closed:
            raise DriverError("driver is closed")
