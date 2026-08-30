"""Two clocks.

A large model cannot meet a control deadline, so deliberation and control run at different
rates:

    deliberation  ~1-10Hz    policy produces an action chunk (Intent)
    control       100Hz+     each action in that chunk is executed in turn

The scheduler owns the boundary. It asks the policy for intent, checks every action through
safety, applies it, and raises interrupts when confidence drops or a limit is breached.

This split is also what makes the shell possible: the action chunk is the artifact an
operator reviews, and it exists because of a latency constraint rather than for the
interface.

## What this module does not do

**It does not know how a chunk is produced.** `Policy` may be a VLA behind an async
inference engine, a scripted controller, or a replayed demonstration. The scheduler cannot
tell, and that is what makes evaluation against a fixed baseline possible. See
`docs/decisions/0005-wrap-rtc-at-the-service-layer-not-the-kernel.md`.

**It does not decide what an interrupt means.** Waiting for a human is not a kernel
concern, so resolution is delegated to an `InterruptHandler`. The kernel supplies the
context and applies the answer.

**It is synchronous.** Asynchronous chunk production is what LeRobot's RTC engine does
well, and duplicating it badly here would be the worst of both. A synchronous loop runs
MuJoCo faster than real time, which is all v0.1 through v0.3 require.

## The invariant

Every action reaching a driver has passed `safety.check`, including one supplied by an
operator. A human may correct a policy but may not exceed a hard limit. There is exactly
one call site for `driver.apply` in this module, so that invariant is checkable by reading
rather than by trusting.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from tendon.kernel import safety
from tendon.kernel.bus import Bus, SubscriberFailure
from tendon.kernel.interrupt import (
    InterruptMachine,
    InterruptState,
    should_raise,
)
from tendon.kernel.protocols import Driver, Policy
from tendon.kernel.types import (
    Action,
    Intent,
    InterruptContext,
    InterruptReason,
    InterruptResolution,
    Observation,
    Resolution,
    SafetyLimits,
)

__all__ = [
    "EpisodeResult",
    "InterruptHandler",
    "Scheduler",
    "StepRecord",
    "UnsafeCorrection",
]


class UnsafeCorrection(RuntimeError):
    """An operator correction breached a hard limit and could not be clamped.

    Raised rather than silently dropped: the shell must be told that what a human asked
    for was refused, or the operator will believe their correction was applied.
    """


class InterruptHandler(Protocol):
    """Whoever answers when control is handed over.

    In production this is the shell, waiting on an operator. In tests it is a stub. The
    kernel does not wait for humans — it hands over a context and applies the answer.
    """

    def resolve(self, context: InterruptContext) -> InterruptResolution:
        """Decide what happens. Blocking is the caller's problem, not the kernel's."""
        ...


@dataclass(frozen=True)
class StepRecord:
    """One control step, as it happened.

    `commanded` and `applied` differ whenever the body clipped. Both are kept because
    recording only the commanded action would store what the policy asked for as though it
    were the outcome — see the `Driver.apply` contract.
    """

    step: int
    observation: Observation
    commanded: Action
    applied: Action
    #: Set when safety clamped the action before it reached the driver.
    clamped: bool = False
    #: Limits that could not be evaluated for this action.
    unchecked: tuple[str, ...] = ()


@dataclass
class EpisodeResult:
    episode_id: str
    steps: int = 0
    #: Interrupts a human actually resolved.
    interventions: int = 0
    #: Of those, the ones that supplied a replacement intent.
    corrections: int = 0
    #: How the episode ended.
    state: InterruptState = InterruptState.RUNNING
    #: Present when the machine faulted, naming what the saved context lacked.
    fault_reason: tuple[str, ...] = ()
    #: Every limit that went unevaluated at least once, deduplicated. Surfaced so a caller
    #: knows the episode ran partly unverified rather than having to infer it.
    unchecked: tuple[str, ...] = ()
    #: Subscribers that raised and were dropped mid-episode. A run where the recorder died
    #: at step 12 produced 12 steps of data and otherwise looked normal; nobody should have
    #: to discover that by finding a short file later.
    subscriber_failures: tuple[SubscriberFailure, ...] = ()
    records: list[StepRecord] = field(default_factory=list)


@dataclass
class Scheduler:
    """Runs one skill on one body until the episode ends."""

    driver: Driver
    limits: SafetyLimits
    #: Below this confidence, hand over. Ignored when the policy reports no source.
    confidence_threshold: float = 0.5
    handler: InterruptHandler | None = None
    #: Every control step is published here. The recorder, the shell stream and anything
    #: else subscribe. Design decision 1 is structural because of this: recording is not a
    #: mode that can be switched off, it is a subscriber that is always attached.
    bus: Bus[StepRecord] | None = None

    def run_episode(
        self,
        policy: Policy,
        *,
        max_steps: int = 1000,
        seed: int | None = None,
    ) -> EpisodeResult:
        """Execute one episode and return what happened.

        Returns rather than raises on a normal ending, including an abort or a fault:
        those are outcomes to record, not errors. `UnsafeCorrection` is the one exception,
        because it means a human was told something false.
        """
        result = EpisodeResult(episode_id=uuid.uuid4().hex)
        machine = InterruptMachine()
        unchecked: set[str] = set()

        policy.reset()
        observation = self.driver.reset(seed=seed)
        dt_s = 1.0 / self.driver.capability.control_hz
        previous: Action | None = None

        while result.steps < max_steps:
            intent = policy.predict(observation)

            # ---- deliberation tier: is this worth handing over before it executes?
            if should_raise(intent.confidence, self.confidence_threshold):
                outcome = self._hand_over(
                    machine, InterruptReason.LOW_CONFIDENCE, intent, observation, result
                )
                if outcome is None:
                    break
                intent = outcome

            # ---- control tier
            for action in intent.actions:
                if result.steps >= max_steps:
                    break

                checked = self._check(action, previous, dt_s)
                unchecked.update(checked.unchecked)

                if not checked.allowed:
                    if checked.clamped is not None:
                        action = checked.clamped
                        was_clamped = True
                    else:
                        outcome = self._hand_over(
                            machine,
                            InterruptReason.SAFETY_TRIP,
                            intent,
                            observation,
                            result,
                        )
                        if outcome is None:
                            break
                        # The replacement is re-checked on the next iteration of the
                        # outer loop rather than executed here, so that a corrected
                        # action never reaches a driver without passing safety.
                        intent = outcome
                        break
                else:
                    was_clamped = False

                applied = self.driver.apply(action)
                observation = self.driver.observe()

                record = StepRecord(
                    step=result.steps,
                    observation=observation,
                    commanded=action,
                    applied=applied,
                    clamped=was_clamped,
                    unchecked=checked.unchecked,
                )
                result.records.append(record)
                if self.bus is not None:
                    # Never raises. A subscriber that throws is dropped and reported;
                    # none of them is a reason to stop a moving body.
                    self.bus.publish(record, step=record.step)

                previous = applied
                result.steps += 1

            if machine.is_terminal():
                break

        result.state = machine.state
        result.fault_reason = machine.fault_reason
        result.interventions = machine.interventions
        result.corrections = machine.corrections
        result.unchecked = tuple(sorted(unchecked))
        result.subscriber_failures = self.bus.failures if self.bus is not None else ()
        return result

    # ------------------------------------------------------------------ internals

    def _check(self, action: Action, previous: Action | None, dt_s: float) -> safety.SafetyVerdict:
        return safety.check(
            action,
            self.limits,
            safety.CheckContext(previous=previous, dt_s=dt_s),
        )

    def _hand_over(
        self,
        machine: InterruptMachine,
        reason: InterruptReason,
        intent: Intent,
        observation: Observation,
        result: EpisodeResult,
    ) -> Intent | None:
        """Hand control to a human. Returns the intent to run, or None to stop.

        Returns None on four paths, and they are not the same thing: no handler
        configured, the context was insufficient so the machine faulted, the operator
        aborted, or the operator rejected without supplying a replacement. All four end
        the episode; only the second is a fault, and `EpisodeResult.state` distinguishes
        them.
        """
        if self.handler is None:
            # Nobody to ask. Stopping is the only safe answer — continuing would execute
            # an action the system already judged not worth executing unsupervised.
            return None

        context = InterruptContext(
            episode_id=result.episode_id,
            step=result.steps,
            reason=reason,
            intent=intent,
            observation=observation,
        )

        if machine.raise_interrupt(context) is InterruptState.FAULTED:
            return None

        resolution = self.handler.resolve(context)
        plan = machine.resolve(resolution)
        if plan is None:
            return None

        machine.resumed()

        if plan.use_correction:
            assert resolution.correction is not None  # guaranteed by InterruptMachine
            correction = resolution.correction
            # An operator may correct a policy but may not exceed a hard limit. Checked
            # here so the refusal is reported rather than discovered when the first action
            # of the correction is refused mid-chunk.
            for action in correction.actions:
                verdict = self._check(action, None, 1.0 / self.driver.capability.control_hz)
                if not verdict.allowed and verdict.clamped is None:
                    raise UnsafeCorrection(
                        f"operator correction breaches a limit and cannot be clamped: "
                        f"{list(verdict.violated)}"
                    )
            return correction

        if resolution.resolution is Resolution.APPROVED:
            return intent

        # REJECTED with no correction: the operator declined this plan and offered
        # nothing else. Asking the policy for alternatives is v0.2 shell work; until
        # then, stopping is the honest behaviour.
        return None
