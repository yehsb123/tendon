"""Safety limits are enforced, and unenforceable limits are reported as such.

The second half matters as much as the first. A check that silently passes when it could
not evaluate anything is worse than no check, because it is trusted.

These tests get the same treatment the curation metrics do — they cover what the module
refuses to claim, not only what it catches.
"""

from __future__ import annotations

import pytest

from tendon.kernel.safety import CheckContext, check, check_force
from tendon.kernel.types import Action, ActionSpace, SafetyLimits

VEL_LIMIT = SafetyLimits(max_joint_velocity=1.0)
BOX = SafetyLimits(workspace_min=[-0.4, -0.4, 0.0], workspace_max=[0.4, 0.4, 0.5])


def vel(*values: float) -> Action:
    return Action(space=ActionSpace.JOINT_VELOCITY, values=list(values))


def pos(*values: float) -> Action:
    return Action(space=ActionSpace.JOINT_POSITION, values=list(values))


def ee_abs(x: float, y: float, z: float) -> Action:
    return Action(space=ActionSpace.EE_ABS_POSE, values=[x, y, z, 0.0, 0.0, 0.0])


# --------------------------------------------------------------------------- velocity


def test_velocity_within_limit_is_allowed() -> None:
    verdict = check(vel(0.5, -0.9, 0.1), VEL_LIMIT)
    assert verdict.allowed
    assert not verdict.violated
    assert not verdict.unchecked


def test_velocity_over_limit_is_refused() -> None:
    verdict = check(vel(0.5, -1.4, 0.1), VEL_LIMIT)
    assert not verdict.allowed
    assert any("max_joint_velocity" in v for v in verdict.violated)


def test_velocity_clamp_scales_uniformly() -> None:
    """Scaled, not clipped per joint.

    Clipping one joint changes the shape of the motion — a different trajectory wearing
    the same numbers. Scaling keeps the direction and only slows it down.
    """
    verdict = check(vel(1.0, -2.0, 0.5), VEL_LIMIT)
    assert verdict.clamped is not None

    values = verdict.clamped.values
    assert max(abs(v) for v in values) == pytest.approx(1.0)

    # Direction preserved: every ratio is the same.
    assert values[0] / 1.0 == pytest.approx(values[1] / -2.0)
    assert values[0] / 1.0 == pytest.approx(values[2] / 0.5)


def test_clamped_action_passes_a_second_check() -> None:
    """A clamp that still violates the limit would be a trap for the scheduler."""
    first = check(vel(3.0, -4.0), VEL_LIMIT)
    assert first.clamped is not None
    assert check(first.clamped, VEL_LIMIT).allowed


def test_gripper_survives_clamping() -> None:
    action = Action(space=ActionSpace.JOINT_VELOCITY, values=[5.0], gripper=0.25)
    verdict = check(action, VEL_LIMIT)
    assert verdict.clamped is not None
    assert verdict.clamped.gripper == 0.25


# ------------------------------------------------------- velocity from position commands


def test_position_command_velocity_needs_previous_and_dt() -> None:
    verdict = check(pos(0.0, 0.0), VEL_LIMIT)
    assert verdict.allowed
    assert any("max_joint_velocity" in u for u in verdict.unchecked), (
        "a position command with no history cannot be velocity-checked, and the verdict "
        "must say so rather than passing silently"
    )


def test_position_command_velocity_is_derived_when_possible() -> None:
    ctx = CheckContext(previous=pos(0.0, 0.0), dt_s=0.1)
    # 0.05 rad in 0.1 s = 0.5 rad/s, under the limit.
    assert check(pos(0.05, 0.0), VEL_LIMIT, ctx).allowed
    # 0.2 rad in 0.1 s = 2.0 rad/s, over it.
    assert not check(pos(0.2, 0.0), VEL_LIMIT, ctx).allowed


def test_position_clamp_lands_on_the_ceiling() -> None:
    ctx = CheckContext(previous=pos(0.0, 0.0), dt_s=0.1)
    verdict = check(pos(0.3, 0.0), VEL_LIMIT, ctx)
    assert verdict.clamped is not None
    # Ceiling is 1.0 rad/s over 0.1 s, so the reachable step is 0.1 rad.
    assert verdict.clamped.values[0] == pytest.approx(0.1)


def test_changed_joint_count_is_not_averaged_over() -> None:
    """A joint count change mid-episode is a fault, not something to interpolate."""
    ctx = CheckContext(previous=pos(0.0, 0.0), dt_s=0.1)
    verdict = check(pos(0.0, 0.0, 0.0), VEL_LIMIT, ctx)
    assert any("max_joint_velocity" in u for u in verdict.unchecked)


@pytest.mark.parametrize("dt", [0.0, -0.1])
def test_nonpositive_dt_does_not_divide(dt: float) -> None:
    ctx = CheckContext(previous=pos(0.0), dt_s=dt)
    verdict = check(pos(1.0), VEL_LIMIT, ctx)
    assert verdict.unchecked


# -------------------------------------------------------------------------- workspace


def test_workspace_violation_is_refused() -> None:
    verdict = check(ee_abs(0.9, 0.0, 0.2), BOX)
    assert not verdict.allowed
    assert any("workspace" in v for v in verdict.violated)


def test_workspace_violation_offers_no_clamp() -> None:
    """A clamped target is a different goal.

    Silently substituting a goal is exactly the failure this module exists to prevent,
    so refusing is the only correct answer.
    """
    verdict = check(ee_abs(0.9, 0.0, 0.2), BOX)
    assert verdict.clamped is None


def test_workspace_floor_is_checked_too() -> None:
    verdict = check(ee_abs(0.0, 0.0, -0.05), BOX)
    assert not verdict.allowed
    assert any("z=" in v for v in verdict.violated)


def test_joint_space_cannot_be_workspace_checked() -> None:
    """The kernel has no forward kinematics, and acquiring it would break the boundary.

    Robot geometry is a driver concern. A kernel that knew it would stop being able to
    treat bodies as interchangeable, which is design decision 3.
    """
    verdict = check(pos(0.1, 0.2, 0.3), BOX)
    assert verdict.allowed
    assert any("workspace" in u for u in verdict.unchecked)


def test_delta_pose_needs_current_position() -> None:
    without = check(Action(space=ActionSpace.EE_DELTA_POSE, values=[0.9, 0, 0, 0, 0, 0]), BOX)
    assert any("workspace" in u for u in without.unchecked)

    ctx = CheckContext(ee_position=(0.0, 0.0, 0.2))
    with_pos = check(
        Action(space=ActionSpace.EE_DELTA_POSE, values=[0.9, 0, 0, 0, 0, 0]), BOX, ctx
    )
    assert not with_pos.allowed


# ---------------------------------------------------------------- combined violations


def test_no_partial_clamp_when_workspace_also_breached() -> None:
    """A clamp that fixes one violation and not another invites a caller to trust it."""
    limits = SafetyLimits(
        max_joint_velocity=1.0,
        workspace_min=[-0.1, -0.1, 0.0],
        workspace_max=[0.1, 0.1, 0.1],
    )
    ctx = CheckContext(ee_position=(0.0, 0.0, 0.0))
    action = Action(space=ActionSpace.EE_ABS_POSE, values=[0.9, 0.0, 0.0, 0, 0, 0])

    verdict = check(action, limits, ctx)
    assert not verdict.allowed
    assert verdict.clamped is None


# ------------------------------------------------------------------------------ force


def test_force_over_limit_is_refused() -> None:
    verdict = check_force([2.0, -12.0], SafetyLimits(max_force=10.0))
    assert not verdict.allowed
    assert any("max_force" in v for v in verdict.violated)


def test_force_within_limit_is_allowed() -> None:
    assert check_force([2.0, -8.0], SafetyLimits(max_force=10.0)).allowed


def test_missing_force_sensing_is_reported_not_assumed() -> None:
    verdict = check_force(None, SafetyLimits(max_force=10.0))
    assert verdict.allowed
    assert any("max_force" in u for u in verdict.unchecked)


def test_no_force_limit_means_nothing_to_check() -> None:
    verdict = check_force(None, SafetyLimits())
    assert verdict.allowed
    assert not verdict.unchecked


# ------------------------------------------------------------------------- empty case


def test_no_limits_configured_checks_nothing_and_says_nothing() -> None:
    verdict = check(vel(99.0), SafetyLimits())
    assert verdict.allowed
    assert not verdict.violated
    assert not verdict.unchecked
