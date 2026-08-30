"""The evaluator reports the number the project is judged on, so it is tested for honesty
as much as for arithmetic.

Every convenient rounding, every dropped outlier, every quietly excluded episode moves the
result in the flattering direction, and none of it looks like misconduct from the inside.
These tests exist to make that hard.
"""

from __future__ import annotations

import pytest

from tendon.kernel.types import ConfidenceSource
from tendon.services.evaluator import (
    EpisodeOutcome,
    evaluate,
    intervention_curve,
    is_significant,
)

MEASURED = ConfidenceSource.CHUNK_VARIANCE
SKILL = "grasp/cube-sim"


def outcome(
    n: int,
    *,
    succeeded: bool = True,
    interventions: int = 0,
    corrections: int = 0,
    faulted: bool = False,
    failure_mode: str | None = None,
    source: ConfidenceSource = MEASURED,
) -> EpisodeOutcome:
    return EpisodeOutcome(
        episode_id=f"ep-{n:04d}",
        skill=SKILL,
        succeeded=succeeded,
        interventions=interventions,
        corrections=corrections,
        faulted=faulted,
        failure_mode=failure_mode,
        confidence_source=source,
    )


def run(count: int, **kw) -> list[EpisodeOutcome]:
    return [outcome(i, **kw) for i in range(count)]


# ----------------------------------------------------------------------------- counting


def test_success_rate() -> None:
    result = evaluate(run(6) + run(4, succeeded=False), skill=SKILL)
    assert result.episodes == 10
    assert result.success_rate == pytest.approx(0.6)


def test_intervention_rate_counts_episodes_not_interventions() -> None:
    """The rate is episodes where a human was asked, over episodes."""
    result = evaluate(run(2, interventions=3) + run(8, interventions=0), skill=SKILL)
    assert result.intervention_rate == pytest.approx(0.2)


def test_interventions_per_episode_is_reported_separately() -> None:
    """An operator asked five times in one run is a different situation from five runs
    asking once, and one number cannot say both."""
    result = evaluate(run(2, interventions=3) + run(8, interventions=0), skill=SKILL)
    assert result.interventions_per_episode == pytest.approx(0.6)


def test_failure_modes_are_grouped_and_ordered() -> None:
    outcomes = (
        run(3, succeeded=False, failure_mode="grip slipped")
        + run(1, succeeded=False, failure_mode="approach angle")
        + run(6)
    )
    result = evaluate(outcomes, skill=SKILL)
    assert list(result.failure_modes) == ["grip slipped", "approach angle"]
    assert result.failure_modes["grip slipped"] == 3


# -------------------------------------------------------------------------------- faults


def test_faults_are_excluded_from_interventions_but_still_counted() -> None:
    """Dropping faults silently would make a system that crashes often look like one
    that needs little help."""
    outcomes = run(2, faulted=True, interventions=5) + run(8)
    result = evaluate(outcomes, skill=SKILL)

    assert result.faults == 2
    assert result.intervention_rate == pytest.approx(0.0)
    assert result.episodes == 10, "faults stay in the denominator"


def test_faults_produce_a_caveat() -> None:
    result = evaluate(run(1, faulted=True) + run(9), skill=SKILL)
    assert any("faulted" in c for c in result.caveats)


# ------------------------------------------------------------------------------ honesty


def test_an_empty_set_raises_rather_than_reporting_zero() -> None:
    """0.0 from no episodes and 0.0 from fifty are opposite findings."""
    with pytest.raises(ValueError):
        evaluate([], skill=SKILL)


def test_episodes_from_another_skill_are_refused() -> None:
    mixed = run(5) + [EpisodeOutcome(episode_id="x", skill="other/skill", succeeded=True)]
    with pytest.raises(ValueError):
        evaluate(mixed, skill=SKILL)


def test_a_small_sample_is_labelled() -> None:
    result = evaluate(run(9), skill=SKILL)
    assert any("below the" in c for c in result.caveats)


def test_mixed_estimators_make_the_result_incomparable() -> None:
    """A rate under chunk variance is not comparable to one under a learned head."""
    outcomes = run(5, source=MEASURED) + run(5, source=ConfidenceSource.LEARNED_HEAD)
    result = evaluate(outcomes, skill=SKILL)

    assert not result.is_comparable
    assert any("more than one confidence estimator" in c for c in result.caveats)


def test_no_estimator_means_the_handovers_were_not_policy_initiated() -> None:
    """Without confidence, a handover was a person or a safety trip — not the policy
    raising its own hand, which is the entire claim (ADR 0004)."""
    result = evaluate(run(10, source=ConfidenceSource.NONE), skill=SKILL)

    assert not result.is_comparable
    assert any("operator-initiated" in c for c in result.caveats)


def test_unlabelled_failures_are_flagged() -> None:
    result = evaluate(run(3, succeeded=False) + run(7), skill=SKILL)
    assert any("no mode label" in c for c in result.caveats)


# -------------------------------------------------------------------------------- curve


def test_curve_is_empty_until_a_full_window_exists() -> None:
    """A partial window swings wildly and then settles, which reads as improvement."""
    assert intervention_curve(run(5), window=20) == []


def test_curve_uses_a_trailing_window_not_a_cumulative_rate() -> None:
    """A cumulative rate is dominated by early episodes and keeps falling after
    improvement stops — the exact way this graph could lie.

    Ten interventions then ten clean runs: a trailing window of 10 must reach 0.0.
    """
    outcomes = run(10, interventions=1) + run(10, interventions=0)
    points = intervention_curve(outcomes, window=10)

    assert points[0].intervention_rate == pytest.approx(1.0)
    assert points[-1].intervention_rate == pytest.approx(0.0)


def test_curve_tracks_cumulative_corrections_on_x() -> None:
    outcomes = run(10, interventions=1, corrections=1) + run(10)
    points = intervention_curve(outcomes, window=10)

    assert points[0].cumulative_corrections == 10
    assert points[-1].cumulative_corrections == 10
    assert points[0].cumulative_corrections <= points[-1].cumulative_corrections


def test_curve_carries_success_rate_alongside() -> None:
    """A falling intervention rate is consistent with a policy that improved and with an
    operator who got tired of being asked. Only the success rate separates them."""
    outcomes = run(10, interventions=1) + run(10, succeeded=False)
    points = intervention_curve(outcomes, window=10)

    assert points[-1].intervention_rate == pytest.approx(0.0)
    assert points[-1].success_rate == pytest.approx(0.0), (
        "interventions stopped but so did success — the curve must show that"
    )


def test_curve_excludes_faulted_episodes_from_the_window() -> None:
    outcomes = run(10) + run(10, faulted=True, interventions=3)
    points = intervention_curve(outcomes, window=10)
    assert all(p.intervention_rate == pytest.approx(0.0) for p in points)


def test_window_of_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        intervention_curve(run(10), window=1)


# --------------------------------------------------------------------------- significance


def test_a_small_difference_is_not_significant() -> None:
    before = evaluate(run(30, interventions=1) + run(70), skill=SKILL)
    after = evaluate(run(28, interventions=1) + run(72), skill=SKILL)
    significant, why = is_significant(before, after)

    assert not significant
    assert "noise" in why


def test_a_large_difference_is_significant_and_says_so_quotably() -> None:
    before = evaluate(run(80, interventions=1) + run(20), skill=SKILL)
    after = evaluate(run(10, interventions=1) + run(90), skill=SKILL)
    significant, why = is_significant(before, after)

    assert significant
    assert "fell" in why and "z=" in why


def test_a_rise_is_reported_as_readily_as_a_fall() -> None:
    """A negative result must be as easy to publish as a positive one."""
    before = evaluate(run(10, interventions=1) + run(90), skill=SKILL)
    after = evaluate(run(80, interventions=1) + run(20), skill=SKILL)
    significant, why = is_significant(before, after)

    assert significant
    assert "rose" in why


def test_too_few_episodes_is_never_significant() -> None:
    before = evaluate(run(5, interventions=1) + run(5), skill=SKILL)
    after = evaluate(run(10), skill=SKILL)
    significant, why = is_significant(before, after)

    assert not significant
    assert "episodes" in why


def test_different_estimators_are_never_compared() -> None:
    before = evaluate(run(50, interventions=1, source=MEASURED) + run(50), skill=SKILL)
    after = evaluate(
        run(50, interventions=1, source=ConfidenceSource.LEARNED_HEAD)
        + run(50, source=ConfidenceSource.LEARNED_HEAD),
        skill=SKILL,
    )
    significant, why = is_significant(before, after)

    assert not significant
    assert "estimator" in why


def test_no_variance_is_not_a_finding() -> None:
    """Every episode on the same side in both sets leaves nothing to test against."""
    before = evaluate(run(50), skill=SKILL)
    after = evaluate(run(50), skill=SKILL)
    significant, why = is_significant(before, after)

    assert not significant
    assert "variance" in why
