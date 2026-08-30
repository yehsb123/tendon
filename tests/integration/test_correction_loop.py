"""A correction travelling from an operator to changed behaviour.

This is the path the whole project rests on, and until now nothing tested it end to end.
The Correct button in the shell was sending `rejected` for a while — an operator would
press it, believe they had taught the robot something, and have taught it nothing. Nothing
went red, because no test followed a correction from the decision to the behaviour.

So these tests walk the whole chain:

    policy is unsure → scheduler hands over → operator corrects
      → correction reaches the policy → the same situation no longer interrupts

CPU only. The MuJoCo body is used where a real one matters; the rest runs on stubs so a
failure points at the link that broke rather than at the simulator.
"""

from __future__ import annotations

import pytest

from tendon.kernel.scheduler import Scheduler
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Capability,
    Confidence,
    ConfidenceSource,
    GripperKind,
    Intent,
    InterruptContext,
    InterruptResolution,
    Observation,
    Proprioception,
    Resolution,
    SafetyLimits,
)
from tendon.services.adaptive import (
    AdaptivePolicy,
    CorrectionMemory,
    StochasticPolicy,
    UncertainRegion,
)

DOF = 2
HZ = 100.0
#: Where the stub body sits, and where the policy is unsure. Holding the body still means
#: the situation recurs on every chunk, which is what makes "does it stop asking?" a
#: question with a clean answer.
UNSURE_AT = 0.1


class StillBody:
    """A body that reports the same position no matter what it is told.

    Not a simplification for convenience: holding the situation fixed is what lets the
    test distinguish "the policy learned" from "the arm moved somewhere it was already
    confident about". A moving body would confound the two.
    """

    def __init__(self, at: float = UNSURE_AT) -> None:
        self._at = at
        self.commanded: list[Action] = []

    @property
    def capability(self) -> Capability:
        return Capability(
            body_id="stub:still",
            dof=DOF,
            gripper=GripperKind.PARALLEL,
            control_hz=HZ,
            cameras=("wrist",),
        )

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        return (ActionSpace.JOINT_POSITION,)

    def reset(self, *, seed: int | None = None) -> Observation:
        self.commanded.clear()
        return self.observe()

    def observe(self) -> Observation:
        return Observation(
            step=len(self.commanded),
            proprio=Proprioception(joint_positions=[self._at, 0.0]),
        )

    def apply(self, action: Action) -> Action:
        self.commanded.append(action)
        return action

    def close(self) -> None:
        pass


class CountingOperator:
    """An operator who corrects once and would correct again if asked."""

    def __init__(self, offset: float = 0.05) -> None:
        self._offset = offset
        self.asked = 0
        #: What was actually sent, so a test can check the body received exactly this
        #: rather than a value reconstructed from an assumption about the policy.
        self.sent: list[list[float]] = []

    def resolve(self, context: InterruptContext) -> InterruptResolution:
        self.asked += 1
        corrected = Intent(
            horizon_s=context.intent.horizon_s,
            actions=tuple(
                Action(
                    space=ActionSpace.JOINT_POSITION,
                    values=[v + self._offset for v in action.values],
                )
                for action in context.intent.actions
            ),
            confidence=Confidence(score=1.0, source=ConfidenceSource.CHUNK_VARIANCE),
            goal="operator correction",
        )
        self.sent = [list(action.values) for action in corrected.actions]
        return InterruptResolution(
            resolution=Resolution.CORRECTED,
            correction=corrected,
            note="lift a little on the way through",
        )


def flat(step: int) -> list[float]:
    return [UNSURE_AT, 0.0]


def unsure_policy(memory: CorrectionMemory) -> AdaptivePolicy:
    inner = StochasticPolicy(
        flat,
        control_hz=HZ,
        dof=DOF,
        regions=(UncertainRegion(joint=0, centre=UNSURE_AT, width=0.03, magnitude=0.08),),
        reference_spread=0.004,
        seed=3,
    )
    return AdaptivePolicy(inner, memory=memory)


def run(policy: AdaptivePolicy, body: StillBody, operator, steps: int = 30):
    return Scheduler(
        driver=body,
        limits=SafetyLimits(),
        confidence_threshold=0.5,
        handler=operator,
        on_intervention=policy.learn_from,
    ).run_episode(policy, max_steps=steps)


# ------------------------------------------------------------------- the whole chain


def test_a_correction_reaches_the_policy() -> None:
    """The link the shell was silently breaking.

    `Scheduler.on_intervention` hands `(observation, resolution)` to the learner. Without
    this call nothing is stored, and the loop looks identical from the outside — episodes
    run, interrupts happen, operators answer, and the rate never moves.
    """
    memory = CorrectionMemory(radius=0.05)
    policy = unsure_policy(memory)
    operator = CountingOperator()

    run(policy, StillBody(), operator, steps=10)

    assert operator.asked >= 1, "the policy never handed over, so nothing was tested"
    assert len(memory) >= 1, "an operator corrected and the policy stored nothing"


def test_the_same_situation_stops_asking() -> None:
    """The claim the project makes, at its smallest.

    Same body, same position, same policy. The only difference between the two episodes is
    what an operator taught in the first.
    """
    memory = CorrectionMemory(radius=0.05)
    body = StillBody()
    operator = CountingOperator()

    first = run(unsure_policy(memory), body, operator, steps=10)
    asked_first = operator.asked

    second = run(unsure_policy(memory), body, operator, steps=10)
    asked_second = operator.asked - asked_first

    assert asked_first >= 1
    assert asked_second == 0, (
        f"the operator was asked {asked_second} more times about a situation they had "
        "already corrected"
    )
    assert first.interventions >= 1
    assert second.interventions == 0


def test_an_approval_teaches_nothing() -> None:
    """An approval says the policy was right. There is nothing new to store, so the
    situation must keep asking — otherwise the rate would fall without any information
    having been added, which is the failure the evaluator exists to avoid measuring."""

    class Approver:
        def __init__(self) -> None:
            self.asked = 0

        def resolve(self, context: InterruptContext) -> InterruptResolution:
            self.asked += 1
            return InterruptResolution(resolution=Resolution.APPROVED)

    memory = CorrectionMemory(radius=0.05)
    body = StillBody()
    approver = Approver()

    run(unsure_policy(memory), body, approver, steps=10)
    assert approver.asked >= 1
    assert len(memory) == 0, "an approval was stored as though it were a lesson"


def test_the_corrected_action_is_what_the_body_executes() -> None:
    """A correction that is stored but not run would be a record of a decision nobody
    acted on."""
    memory = CorrectionMemory(radius=0.05)
    body = StillBody()
    operator = CountingOperator(offset=0.05)

    run(unsure_policy(memory), body, operator, steps=6)

    assert body.commanded, "nothing reached the body"
    assert operator.sent, "the operator never sent a correction"

    # Compared against what the operator actually sent, not against a value derived from
    # assumptions about the policy. The proposed chunk is a mean over perturbed samples, so
    # it differs at every step, and a fixed expected number would be testing the fixture.
    executed = [action.values for action in body.commanded]
    assert any(
        all(abs(a - b) < 1e-9 for a, b in zip(sent, got, strict=True))
        for sent in operator.sent
        for got in executed
    ), "the operator's correction was stored but never executed"


def test_a_correction_still_passes_safety() -> None:
    """A human may correct a policy but may not exceed a hard limit.

    Refused loudly rather than dropped: silently discarding it would leave the operator
    believing their correction was applied.
    """
    from tendon.kernel.scheduler import UnsafeCorrection

    class RecklessOperator:
        def resolve(self, context: InterruptContext) -> InterruptResolution:
            return InterruptResolution(
                resolution=Resolution.CORRECTED,
                correction=Intent(
                    horizon_s=0.1,
                    actions=(Action(space=ActionSpace.EE_ABS_POSE, values=[9.0, 0, 0, 0, 0, 0]),),
                    confidence=Confidence(score=1.0, source=ConfidenceSource.CHUNK_VARIANCE),
                ),
            )

    memory = CorrectionMemory(radius=0.05)
    scheduler = Scheduler(
        driver=StillBody(),
        limits=SafetyLimits(workspace_max=[0.1, 0.1, 0.1]),
        confidence_threshold=0.5,
        handler=RecklessOperator(),
    )

    with pytest.raises(UnsafeCorrection):
        scheduler.run_episode(unsure_policy(memory), max_steps=10)


# ------------------------------------------------------------------ through the API


def test_the_api_refuses_a_correction_with_nothing_in_it() -> None:
    """`CORRECTED` with no correction is the shape the shell was sending.

    Downgrading it to an approval would run exactly what the operator meant to replace, so
    the API refuses instead — and the refusal is what a test would have caught months
    earlier than a person would.
    """
    from pathlib import Path

    from fastapi.testclient import TestClient

    from tendon.api.app import create_app

    repo = Path(__file__).resolve().parents[2]
    client = TestClient(create_app(skill_root=repo / "skills"))

    started = client.post(
        "/api/sessions",
        json={
            "skill": str(repo / "skills" / "grasp" / "cube-sim"),
            "body": "mujoco",
            "max_steps": 20,
            "timeout_s": 2.0,
        },
    )
    if started.status_code != 200:
        pytest.skip(f"could not start a session: {started.text}")

    session_id = started.json()["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/decide",
        json={"resolution": "corrected"},
    )

    assert response.status_code == 400
    assert "must carry a correction" in response.json()["detail"]
