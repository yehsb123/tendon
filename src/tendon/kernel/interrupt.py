"""The interrupt protocol — design decision 2.

An E-stop cuts power: context is destroyed, nothing is recorded, nothing is learned. It
treats the human as a failure handler of last resort. An interrupt saves enough state to
resume, hands control to a human, and records what the human did as training data.

## State machine

```
RUNNING ──raise──▶ PENDING ──resolve──▶ RESUMING ──▶ RUNNING
                      │
                      ├──abort────────▶ STOPPED
                      └──insufficient─▶ FAULTED
```

`FAULTED` is the state that makes this honest. If the saved context cannot support a
resume, the event was not an interrupt — it was a fault that happened to look like one.
Reporting it as a normal interrupt would inflate the denominator of the intervention rate,
and that number is the single metric the project is judged on. Distorting it is a research
integrity problem as much as a safety one, so the transition is explicit and irreversible.

## What this module is not

No asyncio, no sockets, no shell. The machine is pure so that every transition is testable
without a running system. The scheduler owns the waiting; this owns the rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from tendon.kernel.types import (
    Confidence,
    ConfidenceSource,
    InterruptContext,
    InterruptReason,
    InterruptResolution,
    Resolution,
)

__all__ = [
    "InterruptError",
    "InterruptMachine",
    "InterruptState",
    "InvalidTransition",
    "ResumePlan",
    "context_deficiencies",
    "should_raise",
]


class InterruptState(str, Enum):
    RUNNING = "running"
    #: Control is with the operator. The deliberation tier is blocked; the control tier
    #: continues holding position, because a body that stops being commanded mid-motion
    #: is not safe.
    PENDING = "pending"
    RESUMING = "resuming"
    #: Episode ended by operator decision. A deliberate outcome, not a failure.
    STOPPED = "stopped"
    #: The context could not support a resume. Never reported as an intervention.
    FAULTED = "faulted"


class InterruptError(RuntimeError):
    """Base for interrupt protocol errors."""


class InvalidTransition(InterruptError):
    """A transition the state machine does not allow was attempted."""


@dataclass(frozen=True)
class ResumePlan:
    """How execution continues after a resolution."""

    #: Step index execution resumes from.
    step: int
    #: True when the operator supplied a replacement intent to run first.
    use_correction: bool
    #: Recorded so the curator can find corrections later — they are the only episodes
    #: containing a recovery from failure, which demonstration data almost never has.
    resolution: Resolution


def should_raise(confidence: Confidence, threshold: float) -> bool:
    """Whether this confidence warrants handing over.

    Takes a `Confidence` rather than a float because the source is part of the decision.
    When no estimator produced the score, there is nothing to compare against a threshold,
    and this returns False — a policy with no confidence estimate falls back to safety-trip
    and operator-request interrupts instead of silently never asking for help. Treating an
    unmeasured score as a measurement is the failure ADR 0003 exists to prevent.

    A fixed threshold is the v0.2 answer and is known to be wrong: confidence is not
    calibrated across skills, so a value that is right for one is noise for another.
    Calibration against intervention outcomes is v0.3 work, and `skill.yaml` records the
    threshold as a starting point rather than a recommendation.

    Strictly below, not at or below: a threshold of 0.0 must never fire, or a skill opting
    out of confidence-based handover would interrupt on every step.
    """
    if confidence.source is ConfidenceSource.NONE:
        return False
    return confidence.score < threshold


def context_deficiencies(context: InterruptContext) -> tuple[str, ...]:
    """What is missing from a saved context that would prevent a resume.

    Empty means the context is sufficient. Anything else means this event must be treated
    as a fault, however it was raised.

    Checked here rather than at resume time on purpose: discovering that a context is
    unusable *after* an operator has spent thirty seconds deciding wastes their attention
    and leaves the body waiting. Better to fault immediately and say so.
    """
    missing: list[str] = []

    if not context.episode_id:
        missing.append("episode_id: cannot associate the interrupt with a run")

    if context.step < 0:
        missing.append("step: no valid resume point")

    if not context.intent.actions:
        missing.append("intent: no action chunk to resume from or replace")

    if context.observation.step != context.step:
        # An observation from a different step describes a different situation. Deciding
        # against it means deciding about something that already passed.
        missing.append(
            f"observation: captured at step {context.observation.step}, "
            f"interrupt raised at step {context.step}"
        )

    if not context.observation.proprio.joint_positions:
        missing.append("observation: no proprioception, so the body state is unknown")

    return tuple(missing)


@dataclass
class InterruptMachine:
    """Tracks one episode through raise, resolve and resume.

    One machine per episode. Nested interrupts are not supported: an operator already
    holding control cannot be interrupted again, and a second raise while PENDING is a
    scheduler bug rather than a situation to model.
    """

    state: InterruptState = InterruptState.RUNNING
    context: InterruptContext | None = None
    #: Every resolution in this episode, in order. The recorder writes these to the
    #: sidecar table, and the evaluator counts them for the intervention rate.
    history: list[InterruptResolution] = field(default_factory=list)
    #: Set when the machine faulted, naming what the context lacked.
    fault_reason: tuple[str, ...] = ()

    # ------------------------------------------------------------------- raise

    def raise_interrupt(self, context: InterruptContext) -> InterruptState:
        """Hand control over, or fault if the context cannot support a resume.

        Returns the resulting state so the caller cannot ignore a fault: a scheduler that
        assumes PENDING after every raise would wait forever on an operator who is never
        going to be asked.
        """
        if self.state is not InterruptState.RUNNING:
            raise InvalidTransition(f"cannot raise an interrupt while {self.state.value}")

        deficiencies = context_deficiencies(context)
        if deficiencies:
            self.state = InterruptState.FAULTED
            self.fault_reason = deficiencies
            self.context = context
            return self.state

        self.state = InterruptState.PENDING
        self.context = context
        return self.state

    # ----------------------------------------------------------------- resolve

    def resolve(self, resolution: InterruptResolution) -> ResumePlan | None:
        """Apply the operator decision. Returns a plan, or None when the episode ends.

        A correction is recorded whether or not it is ultimately executed. The value of an
        intervention is the record of what a human wanted, not only the motion that
        followed.
        """
        if self.state is not InterruptState.PENDING:
            raise InvalidTransition(f"cannot resolve while {self.state.value}")

        assert self.context is not None  # guaranteed by the PENDING invariant
        self.history.append(resolution)

        if resolution.resolution is Resolution.ABORTED:
            self.state = InterruptState.STOPPED
            return None

        if resolution.resolution is Resolution.CORRECTED and resolution.correction is None:
            # Saying "corrected" without supplying a correction leaves nothing to run.
            # Treated as a protocol error rather than silently downgraded to approval,
            # since approving what the operator meant to replace is the dangerous reading.
            raise InvalidTransition("resolution is CORRECTED but no correction was supplied")

        self.state = InterruptState.RESUMING
        return ResumePlan(
            step=self.context.step,
            use_correction=resolution.resolution is Resolution.CORRECTED,
            resolution=resolution.resolution,
        )

    def resumed(self) -> InterruptState:
        """Confirm execution has restarted."""
        if self.state is not InterruptState.RESUMING:
            raise InvalidTransition(f"cannot resume while {self.state.value}")
        self.state = InterruptState.RUNNING
        self.context = None
        return self.state

    # -------------------------------------------------------------- accounting

    @property
    def interventions(self) -> int:
        """Interrupts a human actually resolved.

        The numerator of the intervention rate. Faults are excluded by construction — a
        faulted machine never reaches PENDING, so no resolution is ever recorded for it.
        Counting faults here would make the system look like it needed more human help
        than it did, which is the same distortion as hiding them, in the other direction.
        """
        return len(self.history)

    @property
    def corrections(self) -> int:
        """Resolutions that supplied a replacement intent.

        The x-axis of the graph the project lives on: cumulative corrections against
        intervention rate. Approvals are interventions but not corrections — the operator
        was consulted and changed nothing, so there is nothing new to learn from.
        """
        return sum(1 for r in self.history if r.resolution is Resolution.CORRECTED)

    def is_terminal(self) -> bool:
        return self.state in (InterruptState.STOPPED, InterruptState.FAULTED)


def reason_is_operator_initiated(reason: InterruptReason) -> bool:
    """Whether a human asked for this rather than the system raising it.

    Kept separate in the statistics: an operator taking over pre-emptively is not evidence
    that the policy was about to fail, and counting it as such would make the intervention
    rate rise every time someone was being careful.
    """
    return reason is InterruptReason.OPERATOR_REQUEST
