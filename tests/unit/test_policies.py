"""Policies that need no model.

`ReplayPolicy` is the fixed baseline every evaluation depends on, so the properties tested
hardest are the ones that would make it stop being fixed: that it ignores observations,
that it ends rather than holding, and that it reports no confidence.
"""

from __future__ import annotations

import pytest

from tendon.kernel.protocols import Policy, PolicyExhausted
from tendon.kernel.types import (
    Action,
    ActionSpace,
    ConfidenceSource,
    Observation,
    Proprioception,
)
from tendon.services.policies import ReplayPolicy, ScriptedPolicy, sine_sweep

HZ = 100.0


def obs(step: int = 0, joints: list[float] | None = None) -> Observation:
    return Observation(
        step=step,
        proprio=Proprioception(joint_positions=joints if joints is not None else [0.0, 0.0]),
    )


def recording(n: int) -> list[Action]:
    return [Action(space=ActionSpace.JOINT_POSITION, values=[i * 0.01, 0.0]) for i in range(n)]


# ------------------------------------------------------------------------------- replay


def test_replay_satisfies_the_policy_protocol() -> None:
    """A replayed demonstration must be indistinguishable from a model to the scheduler."""
    assert isinstance(ReplayPolicy(recording(5), control_hz=HZ), Policy)


def test_replay_returns_the_recording_in_order() -> None:
    policy = ReplayPolicy(recording(6), control_hz=HZ, chunk_size=3)

    first = policy.predict(obs())
    second = policy.predict(obs())

    assert [a.values[0] for a in first.actions] == pytest.approx([0.0, 0.01, 0.02])
    assert [a.values[0] for a in second.actions] == pytest.approx([0.03, 0.04, 0.05])


def test_replay_ignores_the_observation() -> None:
    """The entire point of a baseline.

    A baseline that reacted to the world would not be fixed, and a moving baseline cannot
    anchor a comparison.
    """
    a = ReplayPolicy(recording(4), control_hz=HZ, chunk_size=2).predict(obs(0, [0.0, 0.0]))
    b = ReplayPolicy(recording(4), control_hz=HZ, chunk_size=2).predict(obs(9, [5.0, -3.0]))

    assert [x.values for x in a.actions] == [x.values for x in b.actions]


def test_replay_ends_rather_than_holding() -> None:
    """Holding at the last action would keep an episode running past the end of the
    recording, and every extra step would land in the denominator of a success rate while
    representing nothing that was ever demonstrated."""
    policy = ReplayPolicy(recording(3), control_hz=HZ, chunk_size=3)
    policy.predict(obs())

    with pytest.raises(PolicyExhausted):
        policy.predict(obs())


def test_replay_can_loop_when_asked_explicitly() -> None:
    policy = ReplayPolicy(recording(2), control_hz=HZ, chunk_size=2, loop=True)
    first = policy.predict(obs())
    second = policy.predict(obs())
    assert [a.values for a in first.actions] == [a.values for a in second.actions]


def test_reset_rewinds_to_the_start() -> None:
    policy = ReplayPolicy(recording(4), control_hz=HZ, chunk_size=4)
    policy.predict(obs())
    policy.reset()
    assert policy.remaining == 4
    assert policy.predict(obs()).actions[0].values[0] == pytest.approx(0.0)


def test_a_short_final_chunk_is_returned_not_padded() -> None:
    """Padding would invent actions that were never recorded."""
    policy = ReplayPolicy(recording(5), control_hz=HZ, chunk_size=3)
    policy.predict(obs())
    tail = policy.predict(obs())
    assert len(tail.actions) == 2


def test_horizon_reflects_the_recording_rate() -> None:
    """An operator judges partly by how fast something is about to happen, so a chunk
    rendered over the wrong span is a misleading preview."""
    intent = ReplayPolicy(recording(10), control_hz=50.0, chunk_size=5).predict(obs())
    assert intent.horizon_s == pytest.approx(0.1)


def test_requires_is_read_from_the_recording() -> None:
    """A recording cannot be replayed into another action space without inventing the
    conversion, so the space is read rather than declared."""
    cartesian = [Action(space=ActionSpace.EE_ABS_POSE, values=[0.0] * 6)]
    assert ReplayPolicy(cartesian, control_hz=HZ).requires == (ActionSpace.EE_ABS_POSE,)


def test_an_empty_recording_is_refused() -> None:
    with pytest.raises(ValueError):
        ReplayPolicy([], control_hz=HZ)


@pytest.mark.parametrize(("chunk", "hz"), [(0, HZ), (-1, HZ), (5, 0.0), (5, -1.0)])
def test_invalid_construction_is_refused(chunk: int, hz: float) -> None:
    with pytest.raises(ValueError):
        ReplayPolicy(recording(3), control_hz=hz, chunk_size=chunk)


# ----------------------------------------------------------------------------- scripted


def test_scripted_satisfies_the_protocol() -> None:
    policy = ScriptedPolicy(sine_sweep(dof=2), control_hz=HZ, dof=2)
    assert isinstance(policy, Policy)


def test_scripted_advances_between_calls() -> None:
    policy = ScriptedPolicy(sine_sweep(dof=2, period_steps=8), control_hz=HZ, dof=2, chunk_size=2)
    first = [a.values[0] for a in policy.predict(obs()).actions]
    second = [a.values[0] for a in policy.predict(obs()).actions]
    assert first != second


def test_scripted_reset_returns_to_the_start() -> None:
    policy = ScriptedPolicy(sine_sweep(dof=2, period_steps=8), control_hz=HZ, dof=2, chunk_size=2)
    first = [a.values for a in policy.predict(obs()).actions]
    policy.reset()
    again = [a.values for a in policy.predict(obs()).actions]
    assert first == again


def test_a_dof_mismatch_is_refused_loudly() -> None:
    """A mismatched action would be clipped by the driver and recorded as though it were
    intended — silently wrong data rather than a visible error."""
    policy = ScriptedPolicy(lambda _: [0.0], control_hz=HZ, dof=3)
    with pytest.raises(ValueError):
        policy.predict(obs())


def test_sine_sweep_moves_only_the_first_joint() -> None:
    fn = sine_sweep(dof=4, amplitude=0.2, period_steps=100)
    values = fn(25)
    assert values[0] == pytest.approx(0.2)
    assert values[1:] == [0.0, 0.0, 0.0]


def test_sine_sweep_stays_within_its_amplitude() -> None:
    """The obvious way to misuse a scripted policy is to point it at real hardware with a
    number chosen for a simulator."""
    fn = sine_sweep(dof=2, amplitude=0.2, period_steps=37)
    assert all(abs(fn(step)[0]) <= 0.2 + 1e-9 for step in range(200))


def test_sine_sweep_needs_at_least_one_joint() -> None:
    with pytest.raises(ValueError):
        sine_sweep(dof=0)


# --------------------------------------------------------------------------- confidence


@pytest.mark.parametrize(
    "policy",
    [
        ReplayPolicy(recording(4), control_hz=HZ),
        ScriptedPolicy(sine_sweep(dof=2), control_hz=HZ, dof=2),
    ],
)
def test_baselines_report_no_confidence(policy: Policy) -> None:
    """A baseline that faked a confidence estimate could appear to raise its own hand,
    which is exactly the capability ADR 0004 says separates tendon from what exists. A
    fake one makes the comparison meaningless."""
    confidence = policy.predict(obs()).confidence
    assert confidence.source is ConfidenceSource.NONE
    assert not confidence.is_measured
    assert confidence.reasons, "saying nothing is not the same as saying there is nothing"
