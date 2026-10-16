"""ADR 0007's rule for two confidence sources, as code.

Spread misses a confidently wrong policy; demonstration distance misses a familiar mistake.
When both are measured a chunk hands over if either is below the threshold, which is the
lower score, never an average. When the second source cannot measure, the result has to
be exactly what the scheduler was given before it existed.
"""

from __future__ import annotations

import pytest

from tendon.kernel.interrupt import should_raise
from tendon.kernel.types import Confidence, ConfidenceSource
from tendon.services.demonstration import combine

THRESHOLD = 0.5


def spread(score: float) -> Confidence:
    return Confidence(score=score, source=ConfidenceSource.CHUNK_VARIANCE, reasons=("spread",))


def distance(score: float) -> Confidence:
    return Confidence(score=score, source=ConfidenceSource.DEMONSTRATION, reasons=("far",))


def unmeasured() -> Confidence:
    return Confidence(score=0.0, source=ConfidenceSource.NONE, reasons=("no index",))


@pytest.mark.parametrize("second", [None, unmeasured()], ids=["no index", "not measured"])
@pytest.mark.parametrize("score", [0.0, 0.3, 0.5, 0.9])
def test_without_a_measured_distance_the_spread_passes_through_untouched(
    score: float, second: Confidence | None
) -> None:
    first = spread(score)
    assert combine(first, second) is first


def test_without_a_measured_distance_an_unmeasured_spread_stays_unmeasured() -> None:
    """A deterministic policy with no index is exactly what it was: NONE, not asked."""
    first = unmeasured()
    assert combine(first, None) is first
    assert combine(first, unmeasured()) is first


@pytest.mark.parametrize(
    ("spread_score", "distance_score", "hands_over"),
    [
        (0.9, 0.2, True),  # confidently wrong: tight samples, nothing like it ever worked
        (0.2, 0.9, True),  # familiar motion, policy unsure
        (0.9, 0.8, False),
        (0.3, 0.3, True),
        # Would average to 0.55 and pass. Either alarm is enough.
        (0.95, 0.15, True),
    ],
)
def test_either_below_the_threshold_hands_over(
    spread_score: float, distance_score: float, hands_over: bool
) -> None:
    combined = combine(spread(spread_score), distance(distance_score))
    assert should_raise(combined, THRESHOLD) is hands_over


def test_the_lower_one_is_returned_with_its_own_source() -> None:
    """Not relabelled: evaluation has to know which estimator produced a score."""
    low = distance(0.2)
    assert combine(spread(0.9), low) is low

    low_spread = spread(0.1)
    assert combine(low_spread, distance(0.7)) is low_spread


def test_a_deterministic_policy_is_scored_by_distance_alone() -> None:
    """Spread cannot measure without samples; distance can, and that is half its point."""
    measured = distance(0.4)
    assert combine(unmeasured(), measured) is measured


def test_a_tie_keeps_the_spread() -> None:
    first = spread(0.4)
    assert combine(first, distance(0.4)) is first
