"""Contracts the kernel defines and other layers implement.

The `Driver` protocol lives here rather than in `drivers/` on purpose. An operating
system defines the driver interface and hardware vendors implement it; the reverse would
mean the kernel depends on whichever driver happens to exist. Keeping the contract in the
kernel is what lets `kernel/` import nothing from `drivers/` — the rule enforced by
`tests/unit/test_boundaries.py`.

Driver implementations import this, register themselves, and are loaded by name.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tendon.kernel.types import Action, ActionSpace, Capability, Observation


@runtime_checkable
class Driver(Protocol):
    """What a body must provide — the embodiment HAL.

    Three kinds of body implement this:

    - a simulator (MuJoCo)
    - a physical robot (SO-101, or anything LeRobot supports)
    - recorded human video, which is read-only

    The third is why this abstraction earns its place. If a human demonstration is a
    body, human video and robot episodes land in the same dataset instead of two
    pipelines that never meet.

    Implementations live in `tendon.drivers`, one module per body, and may import their
    own backend and nothing else from this project beyond the kernel.
    """

    @property
    def capability(self) -> Capability:
        """Declared once at load time and treated as immutable for the session."""
        ...

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        """Action spaces this body can execute, most preferred first."""
        ...

    def reset(self, *, seed: int | None = None) -> Observation:
        """Return the body to a defined starting state and report it."""
        ...

    def observe(self) -> Observation:
        """Current observation. Called at the control rate, so it must not block."""
        ...

    def apply(self, action: Action) -> None:
        """Command one step.

        The kernel has already checked this action against `SafetyLimits`. A driver may
        refuse an action its hardware cannot execute, but must not silently substitute a
        different one: a substituted action would be recorded as though the policy chose
        it, and would poison training.

        Read-only bodies raise `ReadOnlyBody`.
        """
        ...

    def close(self) -> None:
        """Release hardware, sockets and file handles. Must be safe to call twice."""
        ...
