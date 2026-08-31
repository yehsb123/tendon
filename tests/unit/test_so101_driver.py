"""`drivers/so101.py`, the physical arm, without a physical arm.

This driver is the one design decision 3 is aimed at: if a simulated body and a real body
are the same body under one HAL, moving a skill from the bench to the desk is a driver
swap. It has never been connected to hardware, and `SECURITY.md` says every safety limit
here has only ever held in simulation. Until an arm exists, what can be checked is the
translation: units, shapes, refusals, and which calls reach the bus.

## Why these run without LeRobot installed

`so101.py` imports `SOFollower` inside `__init__`, so a fake in `sys.modules` is found
instead of the real package. That matters more than convenience. A test that only runs
where the robot extra happens to be installed would not run in CI at all, and this file
would join the list of things that were green because nobody asked them anything.

The fake answers what the driver actually calls -- `action_features`,
`observation_features`, `get_observation`, `send_action`, `connect`, `disconnect` -- and
nothing else. Where it differs from a real arm the tests say so.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

POS = ".pos"
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
GRIPPER = "gripper"
#: `_GRIPPER_OPEN_DEG` in the driver. Open in arm units, before the radian conversion.
OPEN_DEG = 100.0


class FakeConfig:
    """`SOFollowerConfig`. Records what the driver asked for."""

    def __init__(
        self,
        port: str,
        cameras: dict[str, Any],
        use_degrees: bool,
        max_relative_target: float | None,
    ) -> None:
        self.port = port
        self.cameras = cameras
        self.use_degrees = use_degrees
        self.max_relative_target = max_relative_target
        # Deliberately absent, like the real one: `id` and `calibration_dir` are declared on
        # LeRobot's base RobotConfig and do not survive into SOFollowerConfig.__init__.
        # The driver sets both, and a fake that pre-defined them would hide that.


class FakeArm:
    """`SOFollower`. Holds a position and clips commands the way the real bus does."""

    def __init__(self, config: FakeConfig, *, clip_to: float | None = None) -> None:
        self.config = config
        self.is_connected = False
        self.connects: list[bool] = []
        self.disconnects = 0
        self.sent: list[dict[str, float]] = []
        self._clip_to = clip_to
        self.position = {f"{name}{POS}": 0.25 for name in JOINTS}
        self.position[f"{GRIPPER}{POS}"] = 0.0

    @property
    def action_features(self) -> dict[str, type]:
        return {f"{name}{POS}": float for name in (*JOINTS, GRIPPER)}

    @property
    def observation_features(self) -> dict[str, type]:
        return {**self.action_features, "wrist_cam": object}

    def connect(self, calibrate: bool = True) -> None:
        self.connects.append(calibrate)
        self.is_connected = True

    def disconnect(self) -> None:
        self.disconnects += 1
        self.is_connected = False

    def get_observation(self) -> dict[str, Any]:
        import numpy as np

        return {**self.position, "wrist_cam": np.zeros((4, 4, 3), dtype="uint8")}

    def send_action(self, command: dict[str, float]) -> dict[str, float]:
        """Return what was sent, clipped, which is what the real one does."""
        self.sent.append(dict(command))
        if self._clip_to is None:
            return dict(command)
        return {key: min(value, self._clip_to) for key, value in command.items()}


@pytest.fixture
def lerobot(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Put a fake `lerobot.robots.so_follower` where the driver's import will find it.

    The parent packages have to be present too: `from lerobot.robots.so_follower import X`
    imports the chain before it reads the attribute.
    """
    built: dict[str, Any] = {"arm": None, "clip_to": None, "raises": None}

    def make_arm(config: FakeConfig) -> FakeArm:
        if built["raises"] is not None:
            raise built["raises"]
        built["arm"] = FakeArm(config, clip_to=built["clip_to"])
        return built["arm"]

    module = types.ModuleType("lerobot.robots.so_follower")
    module.SOFollower = make_arm  # type: ignore[attr-defined]
    module.SOFollowerConfig = FakeConfig  # type: ignore[attr-defined]

    robots = types.ModuleType("lerobot.robots")
    robots.so_follower = module  # type: ignore[attr-defined]
    root = types.ModuleType("lerobot")
    root.robots = robots  # type: ignore[attr-defined]

    for name, mod in (
        ("lerobot", root),
        ("lerobot.robots", robots),
        ("lerobot.robots.so_follower", module),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return built


def build(lerobot: dict[str, Any], **kwargs: Any):
    from tendon.drivers.so101 import SO101Driver

    defaults: dict[str, Any] = {"port": "COM4"}
    defaults.update(kwargs)
    return SO101Driver(**defaults)


# ----------------------------------------------------------------------- units


def test_the_arm_is_configured_in_radians(lerobot: dict[str, Any]) -> None:
    """`SOFollowerConfig.use_degrees` defaults to True and `Proprioception` is radians.

    An arm reporting degrees through a field documented as radians would read 90 rad at
    90 degrees -- fourteen revolutions -- and every safety limit would be wrong by a factor
    of 57. The conversion is not done twice; the arm is told to speak radians once.
    """
    build(lerobot)
    assert lerobot["arm"].config.use_degrees is False


def test_the_gripper_is_normalised_to_the_same_scale_as_every_other_body(
    lerobot: dict[str, Any],
) -> None:
    """0 closed, 1 open, so a skill commands a simulated jaw and a real one identically."""
    import math

    driver = build(lerobot)
    lerobot["arm"].position[f"{GRIPPER}{POS}"] = math.radians(OPEN_DEG)

    assert driver.observe().proprio.gripper_open == pytest.approx(1.0)


def test_a_commanded_gripper_leaves_in_arm_units(lerobot: dict[str, Any]) -> None:
    import math

    from tendon.kernel.types import Action, ActionSpace

    driver = build(lerobot)
    driver.apply(Action(space=ActionSpace.JOINT_POSITION, values=[0.0] * 5, gripper=1.0))

    assert lerobot["arm"].sent[-1][f"{GRIPPER}{POS}"] == pytest.approx(math.radians(OPEN_DEG))


# ------------------------------------------------------------------- the bench


def test_reset_reports_where_the_arm_is_and_does_not_move_it(lerobot: dict[str, Any]) -> None:
    """The difference between a simulator and a room with a person in it.

    A simulator's reset teleports the world. Driving to a home pose at the start of every
    episode, through whatever is in front of the arm, is not the physical equivalent of
    that -- it is a motion nobody asked for.
    """
    driver = build(lerobot)
    observation = driver.reset()

    assert lerobot["arm"].sent == [], "reset commanded the arm"
    assert observation.proprio.joint_positions == pytest.approx([0.25] * 5)


def test_apply_reports_what_the_arm_was_sent_not_what_was_asked(
    lerobot: dict[str, Any],
) -> None:
    """The whole reason `Driver.apply` returns an action.

    `max_relative_target` clips a command that asks for too large a step, and on hardware
    request and result differ routinely. An episode that recorded the request would be
    training a policy on its own wishes.
    """
    from tendon.kernel.types import Action, ActionSpace

    lerobot["clip_to"] = 0.1
    driver = build(lerobot)

    applied = driver.apply(Action(space=ActionSpace.JOINT_POSITION, values=[1.5] * 5, gripper=None))

    assert applied.values == pytest.approx([0.1] * 5), "the request came back unclipped"
    assert lerobot["arm"].sent[-1][f"{JOINTS[0]}{POS}"] == pytest.approx(1.5)


def test_connect_false_touches_no_hardware(lerobot: dict[str, Any]) -> None:
    """What `doctor` wants: build the driver, ask its shape, open no port."""
    build(lerobot, connect=False)
    assert lerobot["arm"].connects == []


def test_calibration_is_a_choice_because_it_moves_the_arm(lerobot: dict[str, Any]) -> None:
    """`connect(calibrate=True)` drives the arm through its range to find its stops."""
    build(lerobot, calibrate=False)
    assert lerobot["arm"].connects == [False]


# ------------------------------------------------------------------ the shape


def test_the_shape_is_read_from_the_arm_rather_than_assumed(lerobot: dict[str, Any]) -> None:
    """An SO-100, or a five-motor variant, reports its own shape.

    Hardcoding six would make this driver right about one arm and quietly wrong about the
    next one, which is the opposite of what a body-agnostic layer is for.
    """
    from tendon.kernel.types import GripperKind

    capability = build(lerobot).capability

    assert capability.dof == len(JOINTS), "the gripper is not a degree of freedom"
    assert capability.gripper is GripperKind.PARALLEL
    assert capability.cameras == ("wrist_cam",)
    assert capability.has_force_sensing is False, "current draw is not a force reading"


def test_an_arm_with_no_position_features_is_refused(lerobot: dict[str, Any]) -> None:
    from tendon.drivers.base import DriverError

    class Empty(FakeArm):
        @property
        def action_features(self) -> dict[str, type]:
            return {}

    sys.modules["lerobot.robots.so_follower"].SOFollower = Empty  # type: ignore[attr-defined]
    with pytest.raises(DriverError) as caught:
        build(lerobot)
    assert "no position features" in str(caught.value)


# -------------------------------------------------------------------- refusals


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_an_impossible_control_rate_is_refused(lerobot: dict[str, Any], bad: float) -> None:
    from tendon.drivers.base import DriverError

    with pytest.raises(DriverError):
        build(lerobot, control_hz=bad)


def test_the_wrong_action_space_is_refused(lerobot: dict[str, Any]) -> None:
    """The bus takes goal positions. A velocity command is not a slower position command."""
    from tendon.drivers.base import DriverError
    from tendon.kernel.types import Action, ActionSpace

    driver = build(lerobot)
    with pytest.raises(DriverError):
        driver.apply(Action(space=ActionSpace.JOINT_VELOCITY, values=[0.0] * 5))


def test_a_command_of_the_wrong_width_is_refused(lerobot: dict[str, Any]) -> None:
    """Sending four values to five motors would move four joints and leave one behind."""
    from tendon.drivers.base import DriverError
    from tendon.kernel.types import Action, ActionSpace

    driver = build(lerobot)
    with pytest.raises(DriverError) as caught:
        driver.apply(Action(space=ActionSpace.JOINT_POSITION, values=[0.0] * 4))
    assert "5" in str(caught.value)


def test_a_missing_motor_sdk_names_the_install(lerobot: dict[str, Any]) -> None:
    """LeRobot installs without the Feetech bus; building the robot is what pulls it in.

    An ImportError from four frames inside a vendor package tells an operator something is
    wrong, not what to do about it.
    """
    from tendon.drivers.base import DriverError

    lerobot["raises"] = ImportError("No module named 'feetech'")
    with pytest.raises(DriverError) as caught:
        build(lerobot)
    assert "extra" in str(caught.value)


def test_a_port_that_will_not_open_names_the_way_to_find_one(lerobot: dict[str, Any]) -> None:
    from tendon.drivers.base import DriverError

    class Unreachable(FakeArm):
        def connect(self, calibrate: bool = True) -> None:
            raise OSError("could not open COM4")

    sys.modules["lerobot.robots.so_follower"].SOFollower = Unreachable  # type: ignore[attr-defined]
    with pytest.raises(DriverError) as caught:
        build(lerobot)
    assert "lerobot-find-port" in str(caught.value)


# ---------------------------------------------------------------------- close


def test_close_disconnects_once_and_is_safe_twice(lerobot: dict[str, Any]) -> None:
    driver = build(lerobot)
    driver.close()
    driver.close()
    assert lerobot["arm"].disconnects == 1


def test_a_closed_driver_refuses_rather_than_reading_a_dead_port(
    lerobot: dict[str, Any],
) -> None:
    from tendon.drivers.base import DriverError

    driver = build(lerobot)
    driver.close()
    with pytest.raises(DriverError):
        driver.observe()
