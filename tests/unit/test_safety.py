"""Safety limits are enforced, and unenforceable limits are reported as such.

The second half matters as much as the first. A check that silently passes when it could
not evaluate anything is worse than no check, because it is trusted.

These tests get the same treatment the curation metrics do — they cover what the module
refuses to claim, not only what it catches.
"""

from __future__ import annotations

import dataclasses

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
    with_pos = check(Action(space=ActionSpace.EE_DELTA_POSE, values=[0.9, 0, 0, 0, 0, 0]), BOX, ctx)
    assert not with_pos.allowed


# ---------------------------------------------------------------- combined violations


def test_no_partial_clamp_when_workspace_also_breached() -> None:
    """A clamp that fixes one violation and not another invites a caller to trust it.

    What this can and cannot show. A velocity clamp needs a joint-space command and a
    workspace check needs an end-effector pose, so today no single action produces both,
    and `check()`'s guard against offering a clamp alongside a workspace breach cannot be
    reached. Mutation testing made that visible: inverting the guard survived. The guard
    stays for the day a driver supplies forward kinematics. This test asserts the reason
    `clamped` is None here, so it no longer reads as proof of the guard.
    """
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
    assert any("max_joint_velocity" in u for u in verdict.unchecked), (
        "no clamp because velocity cannot be derived from a pose, not because of the guard"
    )


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


def test_an_empty_force_reading_is_reported_not_passed() -> None:
    """Nothing measured is not the same as nothing over the limit."""
    verdict = check_force([], SafetyLimits(max_force=0.5))

    assert verdict.allowed
    assert any("max_force" in u for u in verdict.unchecked), (
        "an empty reading came back as a passed check with nothing unchecked"
    )


# ------------------------------------------------------------ values, not only verdicts
#
# Added after mutation testing this module with cosmic-ray. Every test above asserted a
# verdict, and every position-mode test started from a previous pose of 0.0. From zero,
# `now - was` and `now + was` are the same number, so velocity derivation and the clamp
# could have added instead of subtracting and nothing here would have failed. The
# workspace tests asserted that something was refused, never which axis, so the ceiling
# comparison could be inverted: the breach just moved to another axis.

FROM = pos(0.5, -0.2)
DT = CheckContext(previous=FROM, dt_s=0.1)


def test_position_velocity_is_the_difference_from_the_previous_pose() -> None:
    # 0.05 rad in 0.1 s on the first joint: 0.5 rad/s, under the 1.0 limit.
    assert check(pos(0.55, -0.2), VEL_LIMIT, DT).allowed

    # 0.2 rad in 0.1 s: 2.0 rad/s, and the message carries that number.
    verdict = check(pos(0.7, -0.2), VEL_LIMIT, DT)
    assert verdict.violated == ("max_joint_velocity: 2.0000 > 1.0000 [rad/s]",)


def test_position_clamp_moves_from_the_previous_pose_not_from_zero() -> None:
    # Deltas (0.2, 0.1) over 0.1 s peak at 2.0 rad/s; halving them lands on the ceiling.
    verdict = check(pos(0.7, -0.1), VEL_LIMIT, DT)

    assert verdict.clamped is not None
    assert verdict.clamped.values == pytest.approx([0.6, -0.15])
    assert check(verdict.clamped, VEL_LIMIT, DT).allowed


def test_a_pose_inside_the_box_is_allowed() -> None:
    """The case an inverted comparison fails, and no test had."""
    verdict = check(ee_abs(0.1, -0.1, 0.3), BOX)

    assert verdict.allowed
    assert not verdict.violated


def test_a_ceiling_breach_names_its_axis_and_value() -> None:
    verdict = check(ee_abs(0.0, 0.45, 0.2), BOX)

    assert verdict.violated == ("workspace: y=0.4500 > 0.4000 [m]",)


def test_a_pose_exactly_on_the_bounds_is_allowed() -> None:
    """Strict comparisons on both sides. On the boundary is inside the box."""
    assert check(ee_abs(0.4, -0.4, 0.5), BOX).allowed
    assert check(ee_abs(-0.4, 0.4, 0.0), BOX).allowed


def test_a_delta_pose_is_added_to_the_current_position() -> None:
    ctx = CheckContext(ee_position=(0.1, 0.0, 0.2))

    inside = Action(space=ActionSpace.EE_DELTA_POSE, values=[0.25, 0, 0, 0, 0, 0])
    assert check(inside, BOX, ctx).allowed, "0.1 + 0.25 = 0.35 is inside a 0.4 box"

    outside = Action(space=ActionSpace.EE_DELTA_POSE, values=[0.35, 0, 0, 0, 0, 0])
    assert check(outside, BOX, ctx).violated == ("workspace: x=0.4500 > 0.4000 [m]",)


def test_a_ceiling_alone_is_still_enforced() -> None:
    """A skill may bound only one side. The check ran only when the condition read `or`;
    as `and` it would skip a ceiling-only skill entirely, and every test used both sides."""
    ceiling_only = SafetyLimits(workspace_max=[0.4, 0.4, 0.5])
    floor_only = SafetyLimits(workspace_min=[-0.4, -0.4, 0.0])

    assert check(ee_abs(0.0, 0.0, 0.6), ceiling_only).violated == (
        "workspace: z=0.6000 > 0.5000 [m]",
    )
    assert check(ee_abs(0.0, 0.0, -0.1), floor_only).violated == (
        "workspace: z=-0.1000 < 0.0000 [m]",
    )


def test_a_position_only_pose_is_checked() -> None:
    """Three values are a position with no orientation, which is enough for a workspace."""
    position_only = Action(space=ActionSpace.EE_ABS_POSE, values=[0.0, 0.0, 0.6])

    assert check(position_only, BOX).violated == ("workspace: z=0.6000 > 0.5000 [m]",)


def test_a_pose_too_short_to_hold_a_position_is_unchecked() -> None:
    """Two values would otherwise be checked on x and y and reported as checked."""
    short = Action(space=ActionSpace.EE_ABS_POSE, values=[0.0, 0.9])
    verdict = check(short, BOX)

    assert verdict.allowed
    assert any("workspace" in u for u in verdict.unchecked)


def test_velocity_with_dt_but_no_previous_pose_is_unchecked() -> None:
    """Each missing input on its own is enough to make velocity underdetermined."""
    verdict = check(pos(0.3, 0.0), VEL_LIMIT, CheckContext(previous=None, dt_s=0.1))

    assert verdict.allowed
    assert any("max_joint_velocity" in u for u in verdict.unchecked)


def test_a_joint_command_stays_unchecked_for_workspace_even_with_a_known_pose() -> None:
    """Knowing where the end effector is says nothing about where a joint command sends
    it. A driver that reports `ee_position` every step must not turn joint values into a
    position by accident."""
    ctx = CheckContext(ee_position=(0.1, 0.0, 0.2))
    verdict = check(pos(0.9, 0.9, 0.9), BOX, ctx)

    assert verdict.allowed
    assert any("workspace" in u for u in verdict.unchecked)


def test_a_position_only_delta_pose_is_checked() -> None:
    ctx = CheckContext(ee_position=(0.1, 0.0, 0.2))
    delta = Action(space=ActionSpace.EE_DELTA_POSE, values=[0.35, 0.0, 0.0])

    assert check(delta, BOX, ctx).violated == ("workspace: x=0.4500 > 0.4000 [m]",)


@pytest.mark.parametrize(
    "previous",
    [vel(0.1, 0.1), ee_abs(0.1, 0.0, 0.2)],
    ids=["after a velocity command", "after a pose command"],
)
def test_position_velocity_needs_a_previous_position(previous: Action) -> None:
    """A velocity or a pose is not a position to subtract from. The command has as many
    values as the previous one, so the length check cannot be what refuses it."""
    command = pos(*[0.9] * len(previous.values))
    verdict = check(command, VEL_LIMIT, CheckContext(previous=previous, dt_s=0.1))

    assert verdict.allowed
    assert any("max_joint_velocity" in u for u in verdict.unchecked)


def test_a_pose_command_has_no_joint_velocity_even_after_a_position() -> None:
    """Six pose values after six joint values: same length, so only the space can stop a
    pose from being differenced against joint angles."""
    previous = pos(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    verdict = check(ee_abs(0.1, 0.0, 0.2), VEL_LIMIT, CheckContext(previous=previous, dt_s=0.1))

    assert verdict.allowed
    assert any("max_joint_velocity" in u for u in verdict.unchecked)


def test_a_context_cannot_be_changed_after_it_is_built() -> None:
    """One context is read by every check in a step. If a check could rewrite `previous`,
    the next check would compare against a pose that was never commanded."""
    ctx = CheckContext(previous=pos(0.0), dt_s=0.1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.dt_s = 1.0  # type: ignore[misc]


def test_fewer_joints_than_before_is_not_averaged_over_either() -> None:
    """The existing test covers a joint appearing. A joint disappearing has to be just as
    unchecked, not a `zip` that raises or a comparison of mismatched joints."""
    ctx = CheckContext(previous=pos(0.0, 0.0, 0.0), dt_s=0.1)
    verdict = check(pos(0.0, 0.0), VEL_LIMIT, ctx)

    assert any("max_joint_velocity" in u for u in verdict.unchecked)


def test_a_gripper_only_command_has_no_joint_velocity_to_exceed() -> None:
    """No joint values, so nothing can be over a joint limit however small."""
    gripper_only = Action(space=ActionSpace.JOINT_VELOCITY, values=[], gripper=1.0)

    assert check(gripper_only, SafetyLimits(max_joint_velocity=0.5)).allowed


def test_force_exactly_at_the_limit_is_allowed() -> None:
    """The limit is a ceiling, reached but not exceeded."""
    assert check_force([10.0], SafetyLimits(max_force=10.0)).allowed


def test_a_short_delta_pose_is_unchecked_rather_than_misread() -> None:
    ctx = CheckContext(ee_position=(0.1, 0.0, 0.2))
    verdict = check(Action(space=ActionSpace.EE_DELTA_POSE, values=[0.1, 0.1]), BOX, ctx)

    assert verdict.allowed
    assert any("workspace" in u for u in verdict.unchecked)


# ------------------------------------------------------------------------- empty case


def test_no_limits_configured_checks_nothing_and_says_nothing() -> None:
    verdict = check(vel(99.0), SafetyLimits())
    assert verdict.allowed
    assert not verdict.violated
    assert not verdict.unchecked
