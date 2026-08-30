"""The interrupt protocol, including the cases where it must refuse to call itself one.

The tests that matter most here are the ones about faulting. Reporting a degraded
interrupt as a normal one inflates the intervention count, and that number is what the
project is judged on — so a bug in this direction looks like a research result.
"""

from __future__ import annotations

import pytest

from tendon.kernel.interrupt import (
    InterruptMachine,
    InterruptState,
    InvalidTransition,
    context_deficiencies,
    reason_is_operator_initiated,
    should_raise,
)
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Confidence,
    Intent,
    InterruptContext,
    InterruptReason,
    InterruptResolution,
    Observation,
    Proprioception,
    Resolution,
)


def make_intent(score: float = 0.3) -> Intent:
    return Intent(
        horizon_s=0.5,
        actions=(Action(space=ActionSpace.JOINT_POSITION, values=[0.0, 0.1]),),
        confidence=Confidence(score=score, reasons=("unfamiliar object",)),
        goal="pick up the cube",
        target="cube",
    )


def make_observation(step: int = 7, joints: list[float] | None = None) -> Observation:
    return Observation(
        step=step,
        proprio=Proprioception(joint_positions=joints if joints is not None else [0.0, 0.1]),
    )


def make_context(step: int = 7, **overrides) -> InterruptContext:
    kwargs = {
        "episode_id": "ep-001",
        "step": step,
        "reason": InterruptReason.LOW_CONFIDENCE,
        "intent": make_intent(),
        "observation": make_observation(step),
    }
    kwargs.update(overrides)
    return InterruptContext(**kwargs)


# ------------------------------------------------------------------------ should_raise


@pytest.mark.parametrize(
    ("score", "threshold", "expected"),
    [
        (0.31, 0.5, True),
        (0.5, 0.5, False),
        (0.9, 0.5, False),
        (0.0, 0.0, False),  # opting out must never fire
        (1.0, 1.0, False),
    ],
)
def test_should_raise(score: float, threshold: float, expected: bool) -> None:
    assert should_raise(score, threshold) is expected


def test_threshold_of_zero_never_fires() -> None:
    """A skill opting out of confidence-based handover must not interrupt every step."""
    assert not should_raise(0.0, 0.0)


def test_out_of_range_confidence_is_rejected() -> None:
    with pytest.raises(ValueError):
        should_raise(1.4, 0.5)


# ------------------------------------------------------------------ context sufficiency


def test_a_complete_context_has_no_deficiencies() -> None:
    assert context_deficiencies(make_context()) == ()


def test_observation_from_a_different_step_is_a_deficiency() -> None:
    """Deciding against an observation from another step is deciding about the past."""
    context = make_context(step=7, observation=make_observation(step=3))
    assert any("observation" in d for d in context_deficiencies(context))


def test_missing_proprioception_is_a_deficiency() -> None:
    context = make_context(observation=make_observation(step=7, joints=[]))
    assert any("proprioception" in d for d in context_deficiencies(context))


def test_missing_episode_id_is_a_deficiency() -> None:
    assert any("episode_id" in d for d in context_deficiencies(make_context(episode_id="")))


# -------------------------------------------------------------------------- transitions


def test_raise_moves_to_pending() -> None:
    machine = InterruptMachine()
    assert machine.raise_interrupt(make_context()) is InterruptState.PENDING


def test_insufficient_context_faults_instead_of_pending() -> None:
    """The central honesty check of this module."""
    machine = InterruptMachine()
    state = machine.raise_interrupt(make_context(step=7, observation=make_observation(step=2)))

    assert state is InterruptState.FAULTED
    assert machine.fault_reason
    assert machine.is_terminal()


def test_a_fault_is_never_counted_as_an_intervention() -> None:
    """Counting faults would make the system look like it needed more help than it did."""
    machine = InterruptMachine()
    machine.raise_interrupt(make_context(episode_id=""))

    assert machine.state is InterruptState.FAULTED
    assert machine.interventions == 0
    assert machine.corrections == 0


def test_cannot_raise_twice() -> None:
    """An operator already holding control cannot be interrupted again."""
    machine = InterruptMachine()
    machine.raise_interrupt(make_context())
    with pytest.raises(InvalidTransition):
        machine.raise_interrupt(make_context(step=8))


def test_cannot_resolve_while_running() -> None:
    with pytest.raises(InvalidTransition):
        InterruptMachine().resolve(InterruptResolution(resolution=Resolution.APPROVED))


def test_cannot_resolve_a_faulted_machine() -> None:
    machine = InterruptMachine()
    machine.raise_interrupt(make_context(episode_id=""))
    with pytest.raises(InvalidTransition):
        machine.resolve(InterruptResolution(resolution=Resolution.APPROVED))


# --------------------------------------------------------------------------- resolution


def test_approve_produces_a_resume_plan_at_the_same_step() -> None:
    machine = InterruptMachine()
    machine.raise_interrupt(make_context(step=7))

    plan = machine.resolve(InterruptResolution(resolution=Resolution.APPROVED))

    assert plan is not None
    assert plan.step == 7
    assert plan.use_correction is False
    assert machine.state is InterruptState.RESUMING


def test_correction_requires_a_correction() -> None:
    """Downgrading this to an approval would run exactly what the operator replaced."""
    machine = InterruptMachine()
    machine.raise_interrupt(make_context())

    with pytest.raises(InvalidTransition):
        machine.resolve(InterruptResolution(resolution=Resolution.CORRECTED))


def test_correction_is_carried_into_the_plan() -> None:
    machine = InterruptMachine()
    machine.raise_interrupt(make_context())

    plan = machine.resolve(
        InterruptResolution(
            resolution=Resolution.CORRECTED,
            correction=make_intent(score=1.0),
            note="approach from the left",
        )
    )

    assert plan is not None
    assert plan.use_correction is True


def test_abort_ends_the_episode() -> None:
    machine = InterruptMachine()
    machine.raise_interrupt(make_context())

    assert machine.resolve(InterruptResolution(resolution=Resolution.ABORTED)) is None
    assert machine.state is InterruptState.STOPPED
    assert machine.is_terminal()


def test_resume_returns_to_running_and_clears_context() -> None:
    machine = InterruptMachine()
    machine.raise_interrupt(make_context())
    machine.resolve(InterruptResolution(resolution=Resolution.APPROVED))

    assert machine.resumed() is InterruptState.RUNNING
    assert machine.context is None


def test_cannot_resume_without_resolving() -> None:
    machine = InterruptMachine()
    machine.raise_interrupt(make_context())
    with pytest.raises(InvalidTransition):
        machine.resumed()


# --------------------------------------------------------------------------- accounting


def test_interventions_and_corrections_are_counted_separately() -> None:
    """An approval is an intervention but not a correction.

    The operator was consulted and changed nothing, so there is nothing new to learn —
    and the graph the project lives on plots corrections, not consultations.
    """
    machine = InterruptMachine()

    for step, resolution in enumerate(
        [
            InterruptResolution(resolution=Resolution.APPROVED),
            InterruptResolution(
                resolution=Resolution.CORRECTED, correction=make_intent(1.0)
            ),
            InterruptResolution(resolution=Resolution.REJECTED),
        ]
    ):
        machine.raise_interrupt(make_context(step=step))
        machine.resolve(resolution)
        machine.resumed()

    assert machine.interventions == 3
    assert machine.corrections == 1


def test_full_cycle_returns_to_running() -> None:
    machine = InterruptMachine()
    assert machine.state is InterruptState.RUNNING

    machine.raise_interrupt(make_context())
    machine.resolve(InterruptResolution(resolution=Resolution.APPROVED))
    machine.resumed()

    assert machine.state is InterruptState.RUNNING
    assert not machine.is_terminal()


def test_operator_initiated_is_distinguishable() -> None:
    """Someone taking over pre-emptively is not evidence the policy was about to fail."""
    assert reason_is_operator_initiated(InterruptReason.OPERATOR_REQUEST)
    assert not reason_is_operator_initiated(InterruptReason.LOW_CONFIDENCE)
