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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

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
    ) -> None:
        """
        Args:
            scene_path: MJCF scene to load. Defaults to the cube-pick scene shipped with
                tendon.
            control_hz: Rate this body accepts setpoints at [Hz]. Each `apply` advances
                physics by 1/control_hz [s], however many solver substeps that takes.
            gripper_actuator: Name of the actuator that closes the gripper. Excluded from
                the arm's joint vector and surfaced through `Action.gripper` instead.
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

        self._reset_key = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_KEY, _RESET_KEYFRAME)
        if self._reset_key < 0:
            raise DriverError(
                f"scene {self._scene_path.name} defines no keyframe named "
                f"{_RESET_KEYFRAME!r}. Resetting by index is unsafe for this body: the "
                f"vendored model contributes keyframes that predate the task objects."
            )

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

    def apply(self, action: Action) -> None:
        """Command one control step.

        Setpoints are written to `ctrl` and physics advances by one control period.

        Values are clipped to each actuator's range. That is a hardware bound rather than
        a policy decision — MuJoCo clips internally regardless, and clipping here keeps
        the commanded value and the executed value from diverging without anyone seeing.
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
        for slot, value in zip(self._arm_actuators, action.values, strict=True):
            low, high = ranges[slot]
            self._data.ctrl[slot] = float(np.clip(value, low, high))  # [rad]

        if action.gripper is not None and self._gripper_actuator is not None:
            low, high = self._gripper_range
            self._data.ctrl[self._gripper_actuator] = low + float(action.gripper) * (high - low)

        for _ in range(self._substeps):
            self._mj.mj_step(self._model, self._data)
        self._step += 1

    def close(self) -> None:
        """Release the model and data. Safe to call twice, as the protocol requires."""
        if self._closed:
            return
        self._closed = True
        self._data = None
        self._model = None

    # --------------------------------------------------------------- internals

    def _require_open(self) -> None:
        if self._closed:
            raise DriverError("driver is closed")
