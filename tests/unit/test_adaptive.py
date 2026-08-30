"""The two pieces that close the loop.

`StochasticPolicy` produces a confidence that can actually fall, and `AdaptivePolicy`
turns a correction into different behaviour. Everything else was in place before these;
without them the loop is a diagram.

So the properties tested hardest are the ones that would let the loop *appear* to close
without closing: confidence that never falls, a recall radius wide enough to answer for
situations it was never given in, and learning from resolutions that carry no information.
"""

from __future__ import annotations

import pytest

from tendon.kernel.types import (
    Action,
    ActionSpace,
    Confidence,
    ConfidenceSource,
    Intent,
    InterruptResolution,
    Observation,
    Proprioception,
    Resolution,
)
from tendon.services.adaptive import (
    AdaptivePolicy,
    CorrectionMemory,
    StochasticPolicy,
    UncertainRegion,
)

HZ = 100.0
DOF = 2


def obs(joint0: float = 0.0) -> Observation:
    return Observation(step=0, proprio=Proprioception(joint_positions=[joint0, 0.0]))


def flat(step: int) -> list[float]:
    """A trajectory that does not move, so any spread is the perturbation."""
    return [0.0, 0.0]


def region(centre: float = 0.1) -> UncertainRegion:
    return UncertainRegion(joint=0, centre=centre, width=0.02, magnitude=0.08)


def policy(*regions: UncertainRegion, reference: float = 0.004) -> StochasticPolicy:
    return StochasticPolicy(
        flat,
        control_hz=HZ,
        dof=DOF,
        regions=regions,
        reference_spread=reference,
        seed=1,
    )


def an_intent(value: float = 0.5) -> Intent:
    return Intent(
        horizon_s=0.1,
        actions=(Action(space=ActionSpace.JOINT_POSITION, values=[value, 0.0]),),
        confidence=Confidence(score=1.0, source=ConfidenceSource.CHUNK_VARIANCE),
    )


# --------------------------------------------------------------------- uncertain region


def test_uncertainty_peaks_at_the_centre() -> None:
    r = region(centre=0.1)
    assert r.weight_at(0.1) == pytest.approx(1.0)
    assert r.weight_at(0.1 + 3 * r.width) < 0.01


def test_a_zero_width_region_makes_nothing_uncertain() -> None:
    assert UncertainRegion(joint=0, centre=0.0, width=0.0, magnitude=1.0).weight_at(0.0) == 0.0


# ------------------------------------------------------------------- stochastic policy


def test_confidence_falls_inside_an_uncertain_region() -> None:
    """The property the whole loop rests on. Without this nothing ever hands over."""
    p = policy(region(centre=0.1))

    outside = p.predict(obs(joint0=0.0)).confidence
    p.reset()
    inside = p.predict(obs(joint0=0.1)).confidence

    assert inside.score < outside.score
    assert inside.score < 0.5, "confidence must fall far enough to cross a threshold"


def test_confidence_is_measured_not_asserted() -> None:
    """It comes from sample spread, so the source says so and evaluation stays comparable."""
    assert policy(region()).predict(obs(0.1)).confidence.source is ConfidenceSource.CHUNK_VARIANCE


def test_a_policy_with_no_uncertain_region_is_confident_everywhere() -> None:
    p = policy()
    assert p.predict(obs(0.0)).confidence.score > 0.9
    p.reset()
    assert p.predict(obs(5.0)).confidence.score > 0.9


def test_the_returned_chunk_is_the_mean_not_one_sample() -> None:
    """A single draw is a worse action than the policy can produce, and the operator would
    be reviewing noise rather than intent.

    Checked by averaging behaviour rather than an absolute bound: more samples must move
    the output closer to the underlying trajectory. An absolute threshold here would be a
    claim about this fixture's random draws, not about the code.
    """
    few = StochasticPolicy(
        flat, control_hz=HZ, dof=DOF, regions=(region(centre=0.0),), samples=3, seed=7
    )
    many = StochasticPolicy(
        flat, control_hz=HZ, dof=DOF, regions=(region(centre=0.0),), samples=60, seed=7
    )

    def deviation(p: StochasticPolicy) -> float:
        values = [a.values[0] for a in p.predict(obs(0.0)).actions]
        return sum(abs(v) for v in values) / len(values)

    assert deviation(many) < deviation(few), (
        "averaging more samples must converge on the underlying trajectory; "
        "returning a single draw would not"
    )


def test_reset_makes_a_run_repeatable() -> None:
    """Evaluation compares runs. An unseeded policy makes every comparison noise."""
    p = policy(region(centre=0.0))
    first = [a.values[0] for a in p.predict(obs(0.0)).actions]
    p.reset()
    second = [a.values[0] for a in p.predict(obs(0.0)).actions]
    assert first == pytest.approx(second)


def test_too_few_samples_is_refused() -> None:
    with pytest.raises(ValueError):
        StochasticPolicy(flat, control_hz=HZ, dof=DOF, samples=2)


# ------------------------------------------------------------------- correction memory


def test_a_correction_is_recalled_where_it_was_given() -> None:
    memory = CorrectionMemory(radius=0.05)
    memory.remember(obs(0.1), an_intent())
    assert memory.recall(obs(0.1)) is not None


def test_a_correction_is_not_recalled_far_away() -> None:
    """A radius wide enough to answer for situations it was never given in would make the
    intervention rate fall for the wrong reason — the exact failure this project is built
    to avoid measuring wrongly."""
    memory = CorrectionMemory(radius=0.05)
    memory.remember(obs(0.1), an_intent())
    assert memory.recall(obs(0.9)) is None


def test_the_nearest_correction_wins() -> None:
    memory = CorrectionMemory(radius=0.05)
    memory.remember(obs(0.10), an_intent(value=1.0))
    memory.remember(obs(0.30), an_intent(value=2.0))

    recalled = memory.recall(obs(0.31))
    assert recalled is not None
    assert recalled.actions[0].values[0] == pytest.approx(2.0)


def test_an_empty_memory_recalls_nothing() -> None:
    assert CorrectionMemory().recall(obs(0.0)) is None


# -------------------------------------------------------------------- adaptive policy


def test_a_recalled_situation_no_longer_asks_for_help() -> None:
    """The mechanism by which the intervention rate falls."""
    inner = policy(region(centre=0.1))
    adaptive = AdaptivePolicy(inner, memory=CorrectionMemory(radius=0.05))

    before = adaptive.predict(obs(0.1)).confidence.score
    adaptive.memory.remember(obs(0.1), an_intent())
    after = adaptive.predict(obs(0.1)).confidence.score

    assert before < 0.5
    assert after > 0.9


def test_it_still_asks_where_nothing_was_taught() -> None:
    """The rate must fall only where a human actually intervened. Falling everywhere would
    make the graph meaningless."""
    inner = policy(region(centre=0.1), region(centre=0.5))
    adaptive = AdaptivePolicy(inner, memory=CorrectionMemory(radius=0.05))
    adaptive.memory.remember(obs(0.1), an_intent())

    assert adaptive.predict(obs(0.5)).confidence.score < 0.5


def test_recalled_confidence_still_names_its_source() -> None:
    adaptive = AdaptivePolicy(policy(region()), memory=CorrectionMemory(radius=0.05))
    adaptive.memory.remember(obs(0.1), an_intent())

    confidence = adaptive.predict(obs(0.1)).confidence
    assert confidence.source is ConfidenceSource.CHUNK_VARIANCE
    assert confidence.reasons, "a recalled action should say why it is confident"


# ------------------------------------------------------------------------- learning


def test_only_a_correction_teaches() -> None:
    """An approval says the policy was right and adds nothing. A rejection says it was
    wrong without saying what to do instead. Treating either as a lesson would move the
    intervention rate with no information having been added.
    """
    adaptive = AdaptivePolicy(policy(region()))

    assert not adaptive.learn_from(obs(0.1), InterruptResolution(resolution=Resolution.APPROVED))
    assert not adaptive.learn_from(obs(0.1), InterruptResolution(resolution=Resolution.REJECTED))
    assert len(adaptive.memory) == 0

    taught = adaptive.learn_from(
        obs(0.1),
        InterruptResolution(resolution=Resolution.CORRECTED, correction=an_intent()),
    )
    assert taught
    assert len(adaptive.memory) == 1


def test_learning_is_what_changes_behaviour() -> None:
    """End to end within the policy: teach, then observe that it no longer asks."""
    adaptive = AdaptivePolicy(policy(region(centre=0.1)), memory=CorrectionMemory(radius=0.05))

    assert adaptive.predict(obs(0.1)).confidence.score < 0.5

    adaptive.learn_from(
        obs(0.1),
        InterruptResolution(resolution=Resolution.CORRECTED, correction=an_intent()),
    )

    assert adaptive.predict(obs(0.1)).confidence.score > 0.9
