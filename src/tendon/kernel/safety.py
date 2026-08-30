"""Hard limits, checked on every action regardless of where it came from.

Independent of the policy on purpose. Safety that lives inside the thing being supervised
is not safety. An operator correction arriving from the shell passes through here on the
same path as a policy action: a human may correct a policy, but may not exceed a limit.

## What this module refuses to pretend

An `Action` on its own does not carry enough information to check every limit, and the
honest thing is to say so rather than return `allowed=True` and let a caller believe the
action was verified.

| Limit | Checkable from | Otherwise |
| --- | --- | --- |
| joint velocity | a velocity command directly, or a position command plus the previous one and dt | reported unchecked |
| workspace | an absolute end-effector pose | reported unchecked — the kernel has no forward kinematics, and acquiring it would mean the kernel knowing about robot geometry, which is a driver concern |
| force | an observation, not an action | `check_force` |

So a verdict carries three things, not two: what was violated, what was clamped, and
**what could not be checked at all**. A caller that ignores `unchecked` is choosing to
proceed unverified, which is a decision it should have to make explicitly.

## Clamping

Only offered where the clamped action still expresses the same intent. A velocity above
the ceiling can be clamped — the motion is the same, slower. A target outside the
workspace cannot, because a clamped target is a different goal, and silently substituting
a goal is exactly the failure this module exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

from tendon.kernel.types import Action, ActionSpace, SafetyLimits, SafetyVerdict

__all__ = ["CheckContext", "check", "check_force"]

# Absolute pose actions are laid out as position first, then orientation. Only the
# position part is bounded by a workspace.
_POSITION_DIMS = 3


@dataclass(frozen=True)
class CheckContext:
    """What the kernel knows beyond the action itself.

    Deliberately not a Pydantic model in `types.py`: this never crosses the API boundary.
    The shell has no use for it, and adding it there would widen the contract the shell
    has to mirror for no benefit.
    """

    #: The action commanded on the previous control step, if any.
    previous: Action | None = None
    #: Control period [s]. Required to derive velocity from position commands.
    dt_s: float | None = None
    #: Current end-effector position [m], when the driver can report one.
    ee_position: tuple[float, float, float] | None = None


def check(
    action: Action,
    limits: SafetyLimits,
    context: CheckContext | None = None,
) -> SafetyVerdict:
    """Evaluate one action against the limits.

    Returns a verdict rather than raising, because the caller decides what a violation
    means: the scheduler clamps where clamping is meaningful and raises a SAFETY_TRIP
    interrupt where it is not.
    """
    ctx = context or CheckContext()
    violated: list[str] = []
    unchecked: list[str] = []
    clamped: Action | None = None

    # ---------------------------------------------------------------- velocity

    if limits.max_joint_velocity is not None:
        velocities = _joint_velocities(action, ctx)
        if velocities is None:
            unchecked.append(
                "max_joint_velocity: needs a velocity command, or a position command "
                "with both the previous action and dt_s"
            )
        else:
            peak = max((abs(v) for v in velocities), default=0.0)
            if peak > limits.max_joint_velocity:
                violated.append(
                    f"max_joint_velocity: {peak:.4f} > {limits.max_joint_velocity:.4f} [rad/s]"
                )
                clamped = _clamp_velocity(action, ctx, limits.max_joint_velocity)

    # --------------------------------------------------------------- workspace

    if limits.workspace_min is not None or limits.workspace_max is not None:
        position = _commanded_position(action, ctx)
        if position is None:
            unchecked.append(
                "workspace: needs an absolute end-effector pose; the kernel has no "
                "forward kinematics for joint-space commands"
            )
        else:
            breaches = _workspace_breaches(position, limits)
            violated.extend(breaches)
            # No clamp offered. A clamped target is a different goal.

    allowed = not violated

    # A clamp only helps if it resolves every violation. When a workspace breach is also
    # present the action stays refused, and offering a partial clamp would invite a
    # caller to apply it and believe the action had been made safe.
    if clamped is not None and any(v.startswith("workspace") for v in violated):
        clamped = None

    return SafetyVerdict(
        allowed=allowed,
        violated=tuple(violated),
        unchecked=tuple(unchecked),
        clamped=clamped,
    )


def check_force(measured: list[float] | None, limits: SafetyLimits) -> SafetyVerdict:
    """Evaluate measured force against the ceiling.

    Separate from `check` because force is an observation, not an action. Folding it into
    the action check would mean the same call sometimes verifies force and sometimes does
    not, depending on what the driver happened to report — and a check with inconsistent
    coverage is worse than no check, because it is trusted.
    """
    if limits.max_force is None:
        return SafetyVerdict(allowed=True)

    if measured is None:
        return SafetyVerdict(
            allowed=True,
            unchecked=("max_force: body reports no force sensing",),
        )

    peak = max((abs(f) for f in measured), default=0.0)
    if peak > limits.max_force:
        return SafetyVerdict(
            allowed=False,
            violated=(f"max_force: {peak:.4f} > {limits.max_force:.4f} [N]",),
        )
    return SafetyVerdict(allowed=True)


# --------------------------------------------------------------------------- internals


def _joint_velocities(action: Action, ctx: CheckContext) -> list[float] | None:
    """Joint velocities [rad/s] implied by this action, or None if underdetermined."""
    if action.space is ActionSpace.JOINT_VELOCITY:
        return list(action.values)

    if action.space is not ActionSpace.JOINT_POSITION:
        # Cartesian commands say nothing about joint rates without inverse kinematics.
        return None

    previous, dt_s = ctx.previous, ctx.dt_s
    if previous is None or dt_s is None or dt_s <= 0.0:
        return None
    if previous.space is not ActionSpace.JOINT_POSITION:
        return None
    if len(previous.values) != len(action.values):
        # A changed joint count mid-episode is a fault, not something to average over.
        return None

    return [(now - was) / dt_s for now, was in zip(action.values, previous.values)]


def _clamp_velocity(action: Action, ctx: CheckContext, ceiling: float) -> Action | None:
    """Scale the whole command down so the fastest joint sits at the ceiling.

    Scaled uniformly rather than clipped per joint: clipping one joint changes the shape
    of the motion, which is a different trajectory wearing the same numbers. Scaling keeps
    the direction and only slows it.
    """
    if action.space is ActionSpace.JOINT_VELOCITY:
        peak = max((abs(v) for v in action.values), default=0.0)
        if peak == 0.0:
            return None
        factor = ceiling / peak
        return Action(
            space=action.space,
            values=[v * factor for v in action.values],
            gripper=action.gripper,
        )

    if action.space is ActionSpace.JOINT_POSITION:
        previous, dt_s = ctx.previous, ctx.dt_s
        if previous is None or dt_s is None or dt_s <= 0.0:
            return None
        deltas = [now - was for now, was in zip(action.values, previous.values)]
        peak = max((abs(d) for d in deltas), default=0.0) / dt_s
        if peak == 0.0:
            return None
        factor = ceiling / peak
        return Action(
            space=action.space,
            values=[was + d * factor for was, d in zip(previous.values, deltas)],
            gripper=action.gripper,
        )

    return None


def _commanded_position(action: Action, ctx: CheckContext) -> list[float] | None:
    """The end-effector position [m] this action commands, or None if unknown."""
    if action.space is ActionSpace.EE_ABS_POSE:
        if len(action.values) < _POSITION_DIMS:
            return None
        return list(action.values[:_POSITION_DIMS])

    if action.space is ActionSpace.EE_DELTA_POSE:
        if ctx.ee_position is None or len(action.values) < _POSITION_DIMS:
            return None
        return [
            current + delta
            for current, delta in zip(ctx.ee_position, action.values[:_POSITION_DIMS])
        ]

    # Joint-space commands need forward kinematics, which is a driver concern. Giving the
    # kernel a kinematic model would make it depend on robot geometry and break the
    # boundary that keeps bodies interchangeable.
    return None


def _workspace_breaches(position: list[float], limits: SafetyLimits) -> list[str]:
    axes = ("x", "y", "z")
    breaches: list[str] = []

    if limits.workspace_min is not None:
        for axis, value, floor in zip(axes, position, limits.workspace_min):
            if value < floor:
                breaches.append(f"workspace: {axis}={value:.4f} < {floor:.4f} [m]")

    if limits.workspace_max is not None:
        for axis, value, ceiling in zip(axes, position, limits.workspace_max):
            if value > ceiling:
                breaches.append(f"workspace: {axis}={value:.4f} > {ceiling:.4f} [m]")

    return breaches
