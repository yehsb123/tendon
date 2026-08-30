"""Hard limits, checked on every action regardless of where it came from.

Independent of the policy on purpose. Safety that lives inside the thing being
supervised is not safety. An operator correction arriving from the shell passes through
here on the same path as a policy action: a human may correct a policy, but may not
exceed a limit.
"""

from __future__ import annotations

from tendon.kernel.types import Action, SafetyLimits, SafetyVerdict


def check(action: Action, limits: SafetyLimits) -> SafetyVerdict:
    """Evaluate one action against the limits.

    Returns a verdict rather than raising, because the caller decides what a violation
    means: the scheduler clamps where clamping is meaningful and raises a SAFETY_TRIP
    interrupt where it is not.

    Clamping is only offered where the clamped action still expresses the same intent.
    A velocity above the ceiling can be clamped; a target outside the workspace cannot,
    because a clamped target is a different goal.
    """
    raise NotImplementedError("v0.1")
