"""An SO-101 arm on a serial port, as a tendon body.

This is the driver design decision 3 is aimed at. `drivers/mujoco.py` simulates an
SO-ARM100 for exactly this reason: if the simulated body and the physical body are the
same body under one HAL, moving a skill from the bench to the desk is a driver swap.
Whether that is true is checkable rather than asserted, and this file is where it gets
checked.

Wraps LeRobot's `SOFollower`, which owns the serial protocol, the motor bus and the
calibration file. None of that is tendon's business; the translation between a
`Capability` and a bus full of Dynamixels is.

## Three things a real arm forces that a simulator does not

**`reset` does not move.** In simulation, reset teleports the world to a keyframe. On a
physical arm the equivalent is driving to a home pose, and doing that on `reset` means an
arm that lurches whenever an episode starts, through whatever happens to be in front of
it. This driver's `reset` reports where the arm already is. Getting it somewhere useful is
an operator's job, or a skill's, and either way it is a motion that should be visible in
the record like any other.

**Units are the arm's, not ours.** `SOFollowerConfig.use_degrees` defaults to True, and
`Proprioception.joint_positions` is documented in radians. A driver that passed degrees
through would report an arm at 90 rad, which is 14 revolutions, and every safety limit
would be wrong by a factor of 57. The conversion happens here, and a configuration that
would silently change the unit is refused.

**Nothing is reversible.** A simulator forgives a bad action; a real arm holds it against
a table. `max_relative_target` in the LeRobot config bounds how far a single command may
move the arm, and `SOFollower.send_action` returns what it actually sent after that clip.
That is the reason `Driver.apply` returns an action at all.

Requires the robot extra:  pip install "tendon-os[robot]"
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
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

#: LeRobot names every joint feature `<motor>.pos`.
_POS_SUFFIX = ".pos"

#: Motor whose position is the gripper rather than an arm joint. The SO-101 calls it this
#: in its own calibration file, so the name comes from the hardware rather than from us.
_GRIPPER_MOTOR = "gripper"

#: SO-101 jaw travel, in the arm's own units. Used to normalise `Action.gripper` into
#: something a skill can command on any body, the same way `drivers/mujoco.py` does.
#: Degrees, because that is what the arm reports.
_GRIPPER_CLOSED_DEG = 0.0
_GRIPPER_OPEN_DEG = 100.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


@register("so101")
class SO101Driver(Driver):
    """A physical SO-101 follower arm.

    Construction opens a serial port. That is a side effect worth knowing about: driver
    discovery imports every module in this package, so the LeRobot import stays inside
    `__init__` and nothing here touches hardware until a caller asks for this body by name.
    """

    def __init__(
        self,
        port: str,
        *,
        robot_id: str = "so101",
        control_hz: float = 30.0,
        cameras: dict[str, Any] | None = None,
        max_relative_target: float | None = 5.0,
        connect: bool = True,
        calibrate: bool = True,
    ) -> None:
        """
        Args:
            port: Serial port the follower arm is on, such as `COM4` or `/dev/ttyACM0`.
                `lerobot-find-port` prints it.
            robot_id: Identifier LeRobot uses to find this arm's calibration file. Two
                SO-101s on one host need two ids, or the second will drive itself with the
                first one's offsets.
            control_hz: Rate this body accepts setpoints at [Hz]. 30 rather than the
                simulator's 100: the bus is serial, a round trip costs milliseconds, and
                claiming a rate the hardware cannot hold would make every timing figure in
                `benchmarks/` a fiction on this body.
            cameras: LeRobot camera configs by name, passed through unchanged.
            max_relative_target: Largest change a single command may make to a joint, in
                the arm's units. **Not a tuning parameter.** It is what stops a policy that
                emits a wild action from throwing the arm across the desk before anyone can
                react, and `send_action` reports what it actually sent after the clip.
                None removes the bound, which is a decision to make deliberately.
            connect: Open the port during construction. False builds the driver without
                touching hardware, which is what a `doctor` check wants.
            calibrate: Run LeRobot's calibration when the arm has none stored. Calibration
                moves the arm.
        """
        try:
            from lerobot.robots.so_follower import SOFollower, SOFollowerConfig
        except ImportError as exc:  # pragma: no cover - depends on the robot extra
            raise DriverError(
                'LeRobot is not installed. Install the robot extra: pip install "tendon-os[robot]"'
            ) from exc

        self._control_hz = float(control_hz)
        if self._control_hz <= 0:
            raise DriverError(f"control_hz must be positive, got {control_hz}")

        # Degrees off, radians on. The alternative is converting on every read and write,
        # which doubles the places a unit error can hide. `Proprioception` is radians and
        # this makes the arm agree rather than translating between two truths.
        config = SOFollowerConfig(
            port=port,
            cameras=cameras or {},
            use_degrees=False,
            max_relative_target=max_relative_target,
        )
        # `id` and `calibration_dir` are declared on LeRobot's base `RobotConfig` and do
        # not survive into `SOFollowerConfig.__init__` as arguments or as defaults, so
        # constructing the config directly leaves them missing and `SOFollower.__init__`
        # raises `AttributeError` on the second one. Set explicitly rather than relying on
        # inheritance that is not there.
        config.id = robot_id
        if not hasattr(config, "calibration_dir"):
            # None means LeRobot's own default location, keyed by robot id.
            config.calibration_dir = None

        if getattr(config, "use_degrees", False):
            raise DriverError(
                "SOFollowerConfig ignored use_degrees=False; refusing to run rather than "
                "report degrees as radians, which would make every safety limit wrong by "
                "a factor of 57"
            )

        try:
            self._robot = SOFollower(config)
        except ImportError as exc:
            # The motor SDK is a separate extra from LeRobot itself, and constructing the
            # robot is what pulls it in. Wrapped so `doctor` and `services/bodies.py` see a
            # DriverError naming the install, rather than an ImportError from four frames
            # inside a vendor package.
            raise DriverError(
                f"the SO-101 motor bus needs an extra LeRobot does not install by default: {exc}"
            ) from exc
        except Exception as exc:
            raise DriverError(f"could not build an SO-101 on {port!r}: {exc}") from exc

        self._port = port
        self._closed = False
        self._step = 0

        if connect:
            try:
                self._robot.connect(calibrate=calibrate)
            except Exception as exc:
                raise DriverError(
                    f"could not connect to an SO-101 on {port!r}: {exc}. "
                    f"`lerobot-find-port` lists the ports it can see."
                ) from exc

        self._index_motors()

    # ------------------------------------------------------------------- setup

    def _index_motors(self) -> None:
        """Split the arm's features into joints and a gripper, once.

        Read from `action_features` rather than assumed, so an SO-100 or a five-motor
        variant reports its own shape instead of this file's idea of one.
        """
        names = [
            key[: -len(_POS_SUFFIX)]
            for key in self._robot.action_features
            if key.endswith(_POS_SUFFIX)
        ]
        if not names:
            raise DriverError(
                f"arm on {self._port!r} declares no position features; got "
                f"{list(self._robot.action_features)}"
            )

        self._gripper_motor = _GRIPPER_MOTOR if _GRIPPER_MOTOR in names else None
        self._joint_motors = [n for n in names if n != self._gripper_motor]
        self._cameras = tuple(
            key
            for key in self._robot.observation_features
            if not key.endswith(_POS_SUFFIX) and not key.endswith("_depth")
        )

    # ---------------------------------------------------------------- contract

    @property
    def capability(self) -> Capability:
        return Capability(
            body_id=f"so101:{self._port}",
            dof=len(self._joint_motors),
            gripper=(GripperKind.PARALLEL if self._gripper_motor is not None else GripperKind.NONE),
            control_hz=self._control_hz,
            cameras=self._cameras,
            # The SO-101 has no force sensor. Current draw correlates with load and is not
            # a force measurement, and reporting one would promise a reading that does not
            # exist.
            has_force_sensing=False,
            readonly=False,
        )

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        """Position control. The bus takes goal positions and nothing else."""
        return (ActionSpace.JOINT_POSITION,)

    def reset(self, *, seed: int | None = None) -> Observation:
        """Report where the arm is. Does not move it.

        A simulator's reset teleports the world; the physical equivalent would drive to a
        home pose through whatever is in front of the arm, at the start of every episode,
        without anyone having asked. Positioning the arm is a motion like any other and
        belongs in the record as one.

        `seed` is accepted to satisfy the protocol and has no meaning for hardware.
        """
        self._require_open()
        self._step = 0
        return self.observe()

    def observe(self) -> Observation:
        """Read the bus. Blocks for a serial round trip, which is why control_hz is 30."""
        self._require_open()
        try:
            reading = self._robot.get_observation()
        except Exception as exc:
            raise DriverError(f"could not read the arm on {self._port!r}: {exc}") from exc

        joints = [float(reading[f"{m}{_POS_SUFFIX}"]) for m in self._joint_motors]  # [rad]

        gripper_open: float | None = None
        if self._gripper_motor is not None:
            raw = float(reading[f"{self._gripper_motor}{_POS_SUFFIX}"])
            gripper_open = self._normalise_gripper(raw)

        return Observation(
            step=self._step,
            t=_now(),
            proprio=Proprioception(
                joint_positions=joints,
                # The bus reports positions, not velocities. Differentiating two reads
                # would produce a number, and on a serial link with variable latency it
                # would be a number about the link rather than about the arm.
                joint_velocities=None,
                gripper_open=gripper_open,
                force=None,
            ),
            frames={name: f"so101://{self._port}/{name}/{self._step}" for name in self._cameras},
            extra={"port": self._port},
        )

    def apply(self, action: Action) -> Action:
        """Command one step, and report what the arm was actually sent.

        `SOFollower.send_action` clips against `max_relative_target` and returns the
        clipped command. Returning that rather than the request is the whole reason
        `Driver.apply` has a return value: on hardware the two differ routinely, and an
        episode that recorded the request would be training a policy on its own wishes.
        """
        self._require_open()
        if action.space is not ActionSpace.JOINT_POSITION:
            raise DriverError(
                f"this body accepts {ActionSpace.JOINT_POSITION.value}, got {action.space.value}"
            )
        if len(action.values) != len(self._joint_motors):
            raise DriverError(
                f"expected {len(self._joint_motors)} joint values, got {len(action.values)}"
            )

        command = {
            f"{motor}{_POS_SUFFIX}": float(value)
            for motor, value in zip(self._joint_motors, action.values, strict=True)
        }
        if action.gripper is not None and self._gripper_motor is not None:
            command[f"{self._gripper_motor}{_POS_SUFFIX}"] = self._denormalise_gripper(
                action.gripper
            )

        try:
            sent = self._robot.send_action(command)
        except Exception as exc:
            raise DriverError(f"could not command the arm on {self._port!r}: {exc}") from exc

        applied = [float(sent[f"{m}{_POS_SUFFIX}"]) for m in self._joint_motors]
        applied_gripper = None
        if action.gripper is not None and self._gripper_motor is not None:
            applied_gripper = self._normalise_gripper(
                float(sent[f"{self._gripper_motor}{_POS_SUFFIX}"])
            )

        self._step += 1
        return Action(space=action.space, values=applied, gripper=applied_gripper)

    def close(self) -> None:
        """Disconnect. Safe to call twice, as the protocol requires.

        LeRobot's config disables torque on disconnect by default, so an arm that is closed
        goes limp rather than holding position against gravity. That is the right default
        for a bench arm and the wrong one for an arm holding something.
        """
        if self._closed:
            return
        self._closed = True
        try:
            if self._robot.is_connected:
                self._robot.disconnect()
        except Exception:  # pragma: no cover - a failed disconnect must not mask a result
            pass

    # -------------------------------------------------------------- extensions

    def render(self) -> dict[str, np.ndarray]:
        """Camera frames from the last bus read, as uint8 HWC arrays.

        Mirrors `MujocoDriver.render()` so a consumer does not branch on body type. Costs
        another read, because LeRobot returns images alongside positions rather than
        separately.
        """
        self._require_open()
        if not self._cameras:
            return {}
        reading = self._robot.get_observation()
        return {name: np.asarray(reading[name], dtype=np.uint8) for name in self._cameras}

    # --------------------------------------------------------------- internals

    def _normalise_gripper(self, raw: float) -> float:
        """Arm units to 0 closed, 1 open, so a skill commands both bodies the same way."""
        low = math.radians(_GRIPPER_CLOSED_DEG)
        high = math.radians(_GRIPPER_OPEN_DEG)
        span = high - low
        return float(np.clip((raw - low) / span, 0.0, 1.0)) if span else 0.0

    def _denormalise_gripper(self, value: float) -> float:
        low = math.radians(_GRIPPER_CLOSED_DEG)
        high = math.radians(_GRIPPER_OPEN_DEG)
        return low + float(np.clip(value, 0.0, 1.0)) * (high - low)

    def _require_open(self) -> None:
        if self._closed:
            raise DriverError("driver is closed")
