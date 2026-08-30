"""Contracts the kernel defines and other layers implement.

These live here rather than beside their implementations on purpose. An operating system
defines the driver interface and hardware vendors implement it; the reverse would mean the
kernel depending on whichever driver happens to exist. Keeping the contracts in the kernel
is what lets `kernel/` import nothing from `drivers/` or `services/` — the rule enforced by
`tests/unit/test_boundaries.py`.

Two protocols: `Driver` is a body, `Policy` is whatever decides what the body should do.
The scheduler holds one of each and knows nothing else about either.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tendon.kernel.types import Action, ActionSpace, Capability, Intent, Observation


@runtime_checkable
class Driver(Protocol):
    """What a body must provide — the embodiment HAL.

    Three kinds of body implement this:

    - a simulator (MuJoCo)
    - a physical robot (SO-101, or anything LeRobot supports)
    - recorded human video, which is read-only

    The third is why this abstraction earns its place. If a human demonstration is a body,
    human video and robot episodes land in the same dataset instead of two pipelines that
    never meet.

    Implementations live in `tendon.drivers`, one module per body, and may import their own
    backend and nothing else from this project beyond the kernel.
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

    def apply(self, action: Action) -> Action:
        """Command one step, and report what the body actually executed.

        The kernel has already checked this action against `SafetyLimits`.

        **Returns the applied action, which may differ from the commanded one.** Real
        bodies clip: an actuator has a range, and a command outside it is executed at the
        bound. MuJoCo does this internally, and LeRobot returns the post-clip action from
        `Robot.send_action` for exactly this reason.

        An earlier version of this contract returned `None` and told drivers not to
        substitute actions. That was wrong in a way that mattered: the substitution
        happens in hardware whether the contract permits it or not, and discarding the
        result means every episode records what the policy asked for rather than what the
        motors did. A policy then trains on its own commands as though they were
        outcomes — the recorder faithfully storing a fiction. Design decision 1 says
        running is collecting; collecting the wrong thing is worse than not collecting.

        A driver must still not substitute an action for a *policy* reason. Reporting a
        hardware bound is not the same as deciding a different motion was better.

        Read-only bodies raise `ReadOnlyBody`.
        """
        ...

    def close(self) -> None:
        """Release hardware, sockets and file handles. Must be safe to call twice."""
        ...


@runtime_checkable
class Policy(Protocol):
    """Whatever decides what the body should do next.

    Wraps a VLA, a scripted controller, a replayed episode — the scheduler cannot tell
    which, and that is the point. A recorded human demonstration replayed through this
    protocol is indistinguishable from a live model, which is what makes evaluation
    against a fixed baseline possible.

    Implementations live in `tendon.services` or in a skill package, never in the kernel.

    ## On confidence

    `predict` returns an `Intent`, which carries a `Confidence`. That is a demand this
    protocol makes and that upstream policies do not currently satisfy: LeRobot exposes
    `select_action` and `predict_action_chunk`, both returning a bare tensor, and no
    policy in it reports how sure it is.

    Since `InterruptReason.LOW_CONFIDENCE` is what makes design decision 2 fire, an
    adapter has to produce that number from somewhere. Where it comes from — ensemble
    disagreement, action-chunk variance, an auxiliary head, out-of-distribution
    detection — is not settled, and pretending otherwise by defaulting to 1.0 would mean
    a system that never asks for help while appearing to have the capability.

    See `docs/decisions/0003-confidence-has-no-upstream-source.md`.
    """

    @property
    def name(self) -> str:
        """Skill reference this policy came from, for the episode record."""
        ...

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        """Action spaces this policy can emit, most preferred first.

        Negotiated against `Driver.accepts` at load time. A mismatch is a load-time
        failure: discovering it mid-episode means a robot is already moving.
        """
        ...

    def reset(self) -> None:
        """Clear per-episode state — action queues, history, recurrent state."""
        ...

    def predict(self, observation: Observation) -> Intent:
        """Produce the next action chunk, with confidence and a stated goal.

        Called at the deliberation rate (roughly 1-10Hz), not the control rate. The chunk
        it returns is what the control tier interpolates over, and what the shell renders
        for an operator to approve — see `docs/architecture.md`, "Two clocks".
        """
        ...
