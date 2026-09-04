"""An interrupt with nobody to answer it is said out loud.

Design decision 2 is *the policy raises its own hand*. The first time a real policy did —
a LoRA adapter on `smolvla_base`, once `tendon calibrate` gave its confidence a scale —
`tendon run` printed:

    steps          0
    ended          running
    interventions  0 (0 corrections)

Three true statements adding up to "nothing happened", for the one event the whole project
exists to produce. The scheduler stopped for exactly the right reason and recorded nothing
about it: `_hand_over` returns None when no handler is attached, which is the only safe
answer, and the caller could not tell that apart from an episode that did nothing.

`interventions` stays 0 and that is also right — nobody intervened. What was missing is
that somebody was *asked*.
"""

from __future__ import annotations

import pytest

from tendon.kernel.scheduler import EpisodeResult, Scheduler
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


class _Body:
    """A body that reports the same pose and accepts anything."""

    def __init__(self) -> None:
        self.applied: list[Action] = []

    @property
    def capability(self) -> Capability:
        return Capability(
            body_id="test:body",
            dof=5,
            gripper=GripperKind.PARALLEL,
            control_hz=100.0,
            simulated=True,
        )

    def reset(self, seed: int | None = None) -> Observation:
        return self.observe()

    def observe(self) -> Observation:
        return Observation(step=0, proprio=Proprioception(joint_positions=[0.0] * 5))

    def apply(self, action: Action) -> Action:
        self.applied.append(action)
        return action

    def close(self) -> None:
        pass


class _Unsure:
    """A policy that reports a real, low, measured confidence."""

    name = "test:unsure"
    requires = (ActionSpace.JOINT_POSITION,)

    def reset(self) -> None:
        pass

    def predict(self, observation: Observation) -> Intent:
        return Intent(
            actions=[Action(space=ActionSpace.JOINT_POSITION, values=[0.1] * 5)],
            horizon_s=0.1,
            confidence=Confidence(score=0.05, source=ConfidenceSource.CHUNK_VARIANCE),
        )


@pytest.fixture
def scheduler() -> tuple[Scheduler, _Body]:
    body = _Body()
    return (
        Scheduler(
            driver=body,
            limits=SafetyLimits(),
            confidence_threshold=0.5,
            handler=None,
        ),
        body,
    )


def test_the_episode_says_why_it_stopped(scheduler) -> None:
    engine, _ = scheduler

    result = engine.run_episode(_Unsure(), max_steps=50)

    assert result.stopped_because, "the episode ended with no account of why"
    assert "low_confidence" in result.stopped_because
    assert "no operator" in result.stopped_because


def test_nothing_moved(scheduler) -> None:
    """The point of raising a hand before the control tier. Continuing would execute an
    action the system had just judged not worth executing unsupervised."""
    engine, body = scheduler

    result = engine.run_episode(_Unsure(), max_steps=50)

    assert result.steps == 0
    assert body.applied == []


def test_it_is_not_counted_as_an_intervention(scheduler) -> None:
    """Nobody intervened. An interrupt raised and an interrupt answered are different
    events, and a count that conflated them would put a point on the v0.3 graph for a
    handover that never happened."""
    engine, _ = scheduler

    result = engine.run_episode(_Unsure(), max_steps=50)

    assert result.interventions == 0
    assert result.corrections == 0


def test_a_confident_policy_is_not_stopped(scheduler) -> None:
    """The property has to fail for the ordinary case or it is not measuring anything."""

    class _Sure(_Unsure):
        def predict(self, observation: Observation) -> Intent:
            return Intent(
                actions=[Action(space=ActionSpace.JOINT_POSITION, values=[0.1] * 5)],
                horizon_s=0.1,
                confidence=Confidence(score=0.99, source=ConfidenceSource.CHUNK_VARIANCE),
            )

    engine, body = scheduler
    result = engine.run_episode(_Sure(), max_steps=5)

    assert result.stopped_because is None
    assert result.steps > 0
    assert body.applied


def test_a_score_with_no_source_cannot_stop_an_episode(scheduler) -> None:
    """ADR 0003: when `source` is NONE the score is not a measurement and must not be
    treated as one. An uncalibrated policy falls back to safety trips and operator
    requests rather than halting on a number that means nothing."""

    class _Unmeasured(_Unsure):
        def predict(self, observation: Observation) -> Intent:
            return Intent(
                actions=[Action(space=ActionSpace.JOINT_POSITION, values=[0.1] * 5)],
                horizon_s=0.1,
                confidence=Confidence(score=0.0, source=ConfidenceSource.NONE),
            )

    engine, body = scheduler
    result = engine.run_episode(_Unmeasured(), max_steps=5)

    assert result.stopped_because is None
    assert body.applied, "a score that is not a measurement stopped the body"


def test_the_report_prints_it() -> None:
    """`_report` showed `stopped_because` nowhere, which is why the run looked empty."""
    from rich.console import Console

    from tendon.cli.reporting import report
    from tendon.kernel.bus import Bus

    result = EpisodeResult(episode_id="abcdef123456")
    result.stopped_because = "low_confidence interrupt at step 0 and no operator"

    console = Console(width=200, record=True)
    report(console, result, Bus())

    # Once. `export_text` clears rich's record buffer, so a second call returns "" and the
    # assertion after it fails for a reason that has nothing to do with the code.
    printed = console.export_text()

    assert "stopped" in printed
    assert "low_confidence" in printed
