"""`services/policy_scripted.py`, the fixed reference the benchmarks measure against.

The point of this module is that it is boring and does not change. A falling
intervention-rate curve means nothing without a fixed reference, so a reference that
quietly drifts would invalidate the comparison it exists to support. These tests are the
thing that keeps it fixed.

This file used to open by calling it "the baseline v0.3 is measured against", which is not
established. `benchmarks/end_to_end.py`, `benchmarks/curation.py` and `examples/01_record`
import this class; `tendon eval`, which prints the intervention rate, builds a different
one -- `services/policies.ScriptedPolicy`, a sine sweep whose own docstring says it is for
cases where the behaviour is irrelevant. Whether those should be the same policy is an
open question in docs/collaboration.md. Until it is answered, this says who calls it
rather than what it proves.

Needs no simulator, no model and no torch: the policy is arithmetic over a list of poses.
"""

from __future__ import annotations

import pytest

from tendon.kernel.protocols import Policy
from tendon.kernel.types import (
    ActionSpace,
    ConfidenceSource,
    Observation,
    Proprioception,
)
from tendon.services.policy_scripted import CUBE_PICK, ScriptedPolicy, Stage

CONTROL_HZ = 100.0


@pytest.fixture
def observation() -> Observation:
    return Observation(step=0, proprio=Proprioception(joint_positions=[0.0] * 5))


def test_it_satisfies_the_policy_protocol() -> None:
    """The claim `kernel/protocols.Policy` makes: a scheduler cannot tell this from a VLA."""
    assert isinstance(ScriptedPolicy(control_hz=CONTROL_HZ), Policy)


def test_confidence_is_absent_rather_than_high(observation: Observation) -> None:
    """A deterministic policy has no opinion about whether it is working.

    Reporting a high score would make a policy that never asks for help
    indistinguishable from one that is always right, which is what `ConfidenceSource`
    exists to prevent.
    """
    intent = ScriptedPolicy(control_hz=CONTROL_HZ).predict(observation)

    assert intent.confidence.source is ConfidenceSource.NONE
    assert intent.confidence.score == 0.0
    assert intent.confidence.reasons


def test_the_plan_length_is_the_sum_of_its_stages() -> None:
    policy = ScriptedPolicy(control_hz=CONTROL_HZ)
    assert policy.plan_steps == sum(stage.steps for stage in CUBE_PICK)


def test_a_chunk_is_the_requested_length(observation: Observation) -> None:
    policy = ScriptedPolicy(control_hz=CONTROL_HZ, chunk_steps=25)
    assert len(policy.predict(observation).actions) == 25


def test_the_horizon_follows_the_body_rate(observation: Observation) -> None:
    slow = ScriptedPolicy(control_hz=10.0, chunk_steps=20).predict(observation)
    fast = ScriptedPolicy(control_hz=100.0, chunk_steps=20).predict(observation)

    assert slow.horizon_s == pytest.approx(2.0)
    assert fast.horizon_s == pytest.approx(0.2)


def test_consecutive_chunks_advance_through_the_plan(observation: Observation) -> None:
    policy = ScriptedPolicy(control_hz=CONTROL_HZ, chunk_steps=10)
    first = policy.predict(observation).actions
    second = policy.predict(observation).actions

    assert first[0].values != second[0].values, "the second chunk repeated the first"


def test_reset_rewinds(observation: Observation) -> None:
    """Episodes have to start the same way, or the baseline is not a baseline."""
    policy = ScriptedPolicy(control_hz=CONTROL_HZ, chunk_steps=10)
    first = policy.predict(observation).actions[0].values
    policy.predict(observation)
    policy.reset()

    assert policy.predict(observation).actions[0].values == first


def test_running_past_the_end_holds_rather_than_raising(observation: Observation) -> None:
    """Ending an episode is the scheduler's decision, made on max_steps or a success check.

    A policy that ran out of plan and started raising would turn a finished task into a
    fault.
    """
    policy = ScriptedPolicy(control_hz=CONTROL_HZ, chunk_steps=50)
    for _ in range(policy.plan_steps // 50 + 5):
        intent = policy.predict(observation)

    assert len(intent.actions) == 50
    assert len({tuple(a.values) for a in intent.actions}) == 1, "the tail should hold still"


def test_every_action_is_joint_position(observation: Observation) -> None:
    policy = ScriptedPolicy(control_hz=CONTROL_HZ)
    assert policy.requires == (ActionSpace.JOINT_POSITION,)
    assert all(a.space is ActionSpace.JOINT_POSITION for a in policy.predict(observation).actions)


def test_the_gripper_stays_normalised(observation: Observation) -> None:
    """0 closed, 1 open on every body. A value outside that would not be commandable."""
    policy = ScriptedPolicy(control_hz=CONTROL_HZ, chunk_steps=50)
    for _ in range(policy.plan_steps // 50 + 1):
        for action in policy.predict(observation).actions:
            assert action.gripper is not None
            assert 0.0 <= action.gripper <= 1.0


def test_the_target_names_the_stage_an_operator_would_see(observation: Observation) -> None:
    intent = ScriptedPolicy(control_hz=CONTROL_HZ).predict(observation)
    assert intent.target in {stage.label for stage in CUBE_PICK}


def test_interpolation_reaches_the_stage_pose() -> None:
    """A stage that does not arrive where it said would place the grasp somewhere else."""
    target = (0.1, 0.2, 0.3, 0.4, 0.5)
    policy = ScriptedPolicy(
        control_hz=CONTROL_HZ,
        stages=(Stage(target, 0.5, 20, "only stage"),),
        start_pose=(0.0, 0.0, 0.0, 0.0, 0.0),
        start_jaw=0.0,
        chunk_steps=20,
    )
    last = policy.predict(
        Observation(step=0, proprio=Proprioception(joint_positions=[0.0] * 5))
    ).actions[-1]

    assert last.values == pytest.approx(list(target))
    assert last.gripper == pytest.approx(0.5)


@pytest.mark.parametrize("bad", [{"control_hz": 0.0}, {"chunk_steps": 0}])
def test_impossible_configurations_are_refused(bad: dict) -> None:
    defaults = {"control_hz": CONTROL_HZ}
    defaults.update(bad)
    with pytest.raises(ValueError):
        ScriptedPolicy(**defaults)
