"""Confidence estimation is load-bearing, so it is tested like the curator.

If this module is uninformative, design decisions 1 and 2 collapse into things LeRobot
already does (ADR 0004). So these tests cover three things:

1. that agreement and disagreement produce different scores
2. that the module refuses to report a number it did not measure
3. that it is honest about the failure it cannot see
"""

from __future__ import annotations

import pytest

from tendon.kernel.types import Action, ActionSpace, ConfidenceSource
from tendon.services.confidence import (
    estimate_from_samples,
    spread_of,
    temporal_agreement,
)

REFERENCE = 0.01


def chunk(*steps: list[float]) -> list[Action]:
    return [Action(space=ActionSpace.JOINT_POSITION, values=s) for s in steps]


def agreeing(n: int = 5) -> list[list[Action]]:
    """Samples that all say the same thing."""
    return [chunk([0.0, 0.0], [0.1, 0.0], [0.2, 0.0]) for _ in range(n)]


def scattered(n: int = 5) -> list[list[Action]]:
    """Samples that disagree, increasingly toward the horizon."""
    return [
        chunk([0.0 + i * 0.02, 0.0], [0.1 + i * 0.05, 0.0], [0.2 + i * 0.09, 0.0]) for i in range(n)
    ]


# ------------------------------------------------------------------------------ spread


def test_identical_samples_have_no_spread() -> None:
    spread = spread_of(agreeing())
    assert spread.weighted == pytest.approx(0.0)
    assert spread.peak == pytest.approx(0.0)


def test_disagreeing_samples_have_spread() -> None:
    assert spread_of(scattered()).weighted > 0.0


def test_spread_reports_where_the_disagreement_is() -> None:
    """An operator deciding in two seconds needs to know which step and which joint."""
    spread = spread_of(scattered())
    assert spread.peak_step == 2  # disagreement grows toward the horizon here
    assert spread.peak_dim == 0


def test_imminent_disagreement_is_reported_separately() -> None:
    """Disagreement about the next action is a different situation from disagreement
    about the far end of the horizon, which will be replaced before it executes."""
    now = [chunk([0.0 + i * 0.1, 0.0], [0.5, 0.0], [0.5, 0.0]) for i in range(5)]
    later = [chunk([0.0, 0.0], [0.5, 0.0], [0.5 + i * 0.1, 0.0]) for i in range(5)]
    assert spread_of(now).imminent > spread_of(later).imminent


def test_imminent_disagreement_weighs_more_than_distant() -> None:
    """The tail is discounted because the next prediction replaces it."""
    now = [chunk([i * 0.1, 0.0], [0.5, 0.0], [0.5, 0.0]) for i in range(5)]
    later = [chunk([0.0, 0.0], [0.5, 0.0], [i * 0.1, 0.0]) for i in range(5)]
    assert spread_of(now).weighted > spread_of(later).weighted


def test_too_few_samples_raises_rather_than_reporting_zero() -> None:
    """Zero spread and no measurement look identical in a float and mean the opposite."""
    with pytest.raises(ValueError):
        spread_of([chunk([0.0]), chunk([0.0])])


def test_ragged_chunks_compare_over_the_shortest() -> None:
    """A step only some samples predicted has nothing to disagree about."""
    ragged = [
        chunk([0.0, 0.0], [0.1, 0.0]),
        chunk([0.0, 0.0], [0.1, 0.0], [0.2, 0.0]),
        chunk([0.0, 0.0], [0.1, 0.0], [0.9, 0.0]),
    ]
    assert spread_of(ragged).weighted == pytest.approx(0.0)


def test_empty_chunks_are_rejected() -> None:
    with pytest.raises(ValueError):
        spread_of([[], [], []])


# ---------------------------------------------------------------------------- estimate


def test_agreement_scores_higher_than_disagreement() -> None:
    high = estimate_from_samples(agreeing(), reference_spread=REFERENCE)
    low = estimate_from_samples(scattered(), reference_spread=REFERENCE)
    assert high.score > low.score


def test_estimate_marks_its_source() -> None:
    result = estimate_from_samples(agreeing(), reference_spread=REFERENCE)
    assert result.source is ConfidenceSource.CHUNK_VARIANCE
    assert result.is_measured


def test_score_stays_in_range() -> None:
    wild = [chunk([i * 1000.0, 0.0], [0.0, 0.0], [0.0, 0.0]) for i in range(5)]
    score = estimate_from_samples(wild, reference_spread=REFERENCE).score
    assert 0.0 <= score <= 1.0


def test_scoring_is_relative_to_the_reference_not_absolute() -> None:
    """A 0.02 rad disagreement is nothing on a coarse gripper and large on a wrist.

    Scoring against a fixed threshold would score the hardware, not the situation — the
    same argument as jerk in the curator.
    """
    coarse = estimate_from_samples(scattered(), reference_spread=1.0)
    fine = estimate_from_samples(scattered(), reference_spread=0.001)
    assert coarse.score > fine.score


def test_reasons_are_given_when_the_score_is_low() -> None:
    """A bare number gives an operator nothing to act on."""
    result = estimate_from_samples(scattered(), reference_spread=0.0001)
    assert result.reasons


def test_confident_agreement_needs_no_explanation() -> None:
    assert estimate_from_samples(agreeing(), reference_spread=REFERENCE).reasons == ()


# ------------------------------------------------------- refusing to report a non-number


def test_a_deterministic_policy_gets_no_confidence() -> None:
    """The trap ADR 0003 names.

    Sampling a deterministic policy twice gives the same chunk, so spread is zero and the
    score would be 1.0 regardless of the situation. A robot that never asks for help looks
    identical to one that is always right, so the source is NONE and handover falls back
    to safety trips and operator requests.
    """
    result = estimate_from_samples(agreeing(), reference_spread=REFERENCE, deterministic=True)
    assert result.source is ConfidenceSource.NONE
    assert not result.is_measured
    assert result.reasons


def test_no_reference_scale_means_no_measurement() -> None:
    """Spread with nothing to compare it against is not a confidence."""
    result = estimate_from_samples(agreeing(), reference_spread=0.0)
    assert result.source is ConfidenceSource.NONE


def test_confidently_wrong_is_not_detected() -> None:
    """The documented hole, asserted rather than assumed.

    Samples agreeing tightly on a motion toward entirely the wrong place score exactly as
    well as samples agreeing on the right one. This is the failure most worth catching and
    the estimator is blind to it. Catching it needs instruction-action consistency, which
    needs a model — ADR 0003, v0.3.
    """
    right = [chunk([0.0, 0.0], [0.1, 0.0]) for _ in range(5)]
    wrong = [chunk([0.0, 0.0], [-0.9, 0.0]) for _ in range(5)]

    a = estimate_from_samples(right, reference_spread=REFERENCE)
    b = estimate_from_samples(wrong, reference_spread=REFERENCE)
    assert a.score == pytest.approx(b.score)


# -------------------------------------------------------------------- temporal agreement


def test_a_continuing_plan_agrees_with_itself() -> None:
    previous = chunk([0.0, 0.0], [0.1, 0.0], [0.2, 0.0], [0.3, 0.0])
    current = chunk([0.2, 0.0], [0.3, 0.0])
    assert temporal_agreement(previous, current, consumed=2) == pytest.approx(0.0)


def test_a_plan_that_changes_under_itself_disagrees() -> None:
    """Compared against the continuing case, not against an absolute number.

    The value is a mean over every step and dimension, so a large disagreement on one
    joint is diluted by the joints that did not move. That dilution is intended — a plan
    that changed on one axis did change less than one that changed on all of them — but it
    means an absolute threshold here would be a claim about this fixture rather than about
    the measure.
    """
    previous = chunk([0.0, 0.0], [0.1, 0.0], [0.2, 0.0], [0.3, 0.0])
    continuing = chunk([0.2, 0.0], [0.3, 0.0])
    changed = chunk([0.9, 0.0], [1.4, 0.0])

    steady = temporal_agreement(previous, continuing, consumed=2)
    diverged = temporal_agreement(previous, changed, consumed=2)

    assert steady is not None and diverged is not None
    assert diverged > steady
    assert diverged > 0.4


def test_no_overlap_is_reported_as_none_not_as_agreement() -> None:
    """Nothing to compare is not the same as perfect agreement."""
    previous = chunk([0.0, 0.0], [0.1, 0.0])
    assert temporal_agreement(previous, chunk([0.5, 0.0]), consumed=2) is None


def test_negative_consumed_is_rejected() -> None:
    with pytest.raises(ValueError):
        temporal_agreement(chunk([0.0]), chunk([0.0]), consumed=-1)
