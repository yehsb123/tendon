"""Success is judged from the world, not from what the policy saw.

A skill declares success as a condition on the world — `cube_height_above: 0.1`. The judge
read that from `Observation.extra`, and `drivers/mujoco.py` never put it there, so every
evaluation this project has ever run answered *unknown* for every episode. Half the v0.3
claim — that the robot still did the task — had never been measured once.

The obvious repair was to put the cube's height in `Observation.extra`, and it is wrong for
a reason the driver had already written down beside `body_position`:

    A policy must not call this. Ground-truth object positions are available in simulation
    and not on hardware, so a policy that used them would work in MuJoCo and fail on an
    SO-101 in a way no simulation test could catch. `Observation` is what a policy sees;
    this is what a judge sees.

There was no channel for the second sentence. `MeasuresWorld` is that channel: the
scheduler asks the body when the episode ends, the result carries it, the policy is never
handed it at all.
"""

from __future__ import annotations

import pytest

from tendon.kernel.protocols import MeasuresWorld
from tendon.kernel.scheduler import Scheduler
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Capability,
    Confidence,
    ConfidenceSource,
    GripperKind,
    Intent,
    Observation,
    Proprioception,
    SafetyLimits,
)


class _Blind:
    """A body that cannot see the world. Every real arm."""

    def __init__(self) -> None:
        self.seen: list[Observation] = []

    @property
    def capability(self) -> Capability:
        return Capability(body_id="test:body", dof=5, control_hz=100.0, simulated=True)

    def reset(self, seed: int | None = None) -> Observation:
        return self.observe()

    def observe(self) -> Observation:
        return Observation(step=0, proprio=Proprioception(joint_positions=[0.0] * 5))

    def apply(self, action: Action) -> Action:
        return action

    def close(self) -> None:
        pass


class _Sighted(_Blind):
    """A simulator, which knows where everything is."""

    @property
    def capability(self) -> Capability:
        return Capability(
            body_id="sim:body",
            dof=5,
            gripper=GripperKind.PARALLEL,
            control_hz=100.0,
            simulated=True,
        )

    def world_facts(self) -> dict[str, float]:
        return {"cube_height": 0.42}


class _Policy:
    name = "test:policy"
    requires = (ActionSpace.JOINT_POSITION,)

    def __init__(self) -> None:
        self.observations: list[Observation] = []

    def reset(self) -> None:
        pass

    def predict(self, observation: Observation) -> Intent:
        self.observations.append(observation)
        return Intent(
            actions=[Action(space=ActionSpace.JOINT_POSITION, values=[0.0] * 5)],
            horizon_s=0.1,
            confidence=Confidence(score=0.9, source=ConfidenceSource.CHUNK_VARIANCE),
        )


def _run(body) -> tuple:
    policy = _Policy()
    engine = Scheduler(driver=body, limits=SafetyLimits(), confidence_threshold=0.5)
    return engine.run_episode(policy, max_steps=3), policy


def test_a_body_that_can_see_is_recognised_without_naming_its_class() -> None:
    assert isinstance(_Sighted(), MeasuresWorld)
    assert not isinstance(_Blind(), MeasuresWorld)


def test_the_world_reaches_the_result() -> None:
    result, _ = _run(_Sighted())

    assert result.final_world == {"cube_height": 0.42}


def test_the_policy_is_never_shown_it() -> None:
    """The whole reason this is not in `Observation`. A policy that learned to read ground
    truth would work in simulation and fail on hardware that has none, and no simulation
    test could catch it, because in simulation it is always there."""
    _, policy = _run(_Sighted())

    assert policy.observations, "the policy never ran"
    for observation in policy.observations:
        assert "cube_height" not in observation.extra


def test_a_body_that_cannot_see_reports_nothing_rather_than_zero() -> None:
    """Empty is *unknown*, and `judge` answers None to it. Zero would be a measurement, and
    `cube_height: 0.0` reads as a cube on the floor rather than as a rig that cannot say."""
    result, _ = _run(_Blind())

    assert result.final_world == {}


def test_a_body_that_raises_while_being_asked_still_produced_an_episode() -> None:
    """Losing the episode to keep the verdict is the wrong trade. The episode happened."""

    class _Broken(_Sighted):
        def world_facts(self) -> dict[str, float]:
            raise RuntimeError("sensor died")

    result, _ = _run(_Broken())

    assert result.steps > 0
    assert result.final_world == {}


def test_the_verdict_comes_from_the_world_not_the_observation() -> None:
    """Asserted on the evaluator's actual answer, so the two channels cannot be confused
    by a caller passing the wrong one."""
    from tendon.services.evaluator import SuccessCriterion, judge

    criteria = [SuccessCriterion.parse("cube_height_above", 0.1)]
    result, _ = _run(_Sighted())

    verdict, _reason = judge(result.final_world, criteria)
    assert verdict is True

    unknown, reason = judge({}, criteria)
    assert unknown is None, "an unmeasured world is not a failure"
    assert reason and "cube_height" in reason


@pytest.mark.parametrize("scene", ["so_arm100_cube.xml", "xarm7_cube.xml"])
def test_the_real_driver_names_the_scene_s_objects(scene: str) -> None:
    """From the model, not a table. Two different arms, the same skill, the same key —
    and neither driver has the word "cube" written in it."""
    pytest.importorskip("mujoco")

    from pathlib import Path

    from tendon.services.bodies import open_body

    root = Path(__file__).resolve().parents[2] / "src" / "tendon" / "assets" / "scenes"
    body = open_body("mujoco", scene_path=str(root / scene))
    try:
        body.reset(seed=0)
        facts = body.world_facts()
    finally:
        body.close()

    assert "cube_height" in facts
    assert facts["cube_height"] > 0.0
    # The arm's own links are not objects a skill judges.
    assert not any(key.startswith("Base") or key.startswith("world") for key in facts)
