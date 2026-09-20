"""What a threshold would have cost, asked of episodes that already ran.

ADR 0003's postscript splits calibration in two. `tendon calibrate` measures the **scale**
— how much disagreement is typical — and it shipped. The **threshold** needs what goes
wrong when nobody is asked, which is a property of outcomes and not of spreads.

The blocking piece was never labels. It was that the score at each step was not written
down anywhere: the sidecar's `confidence` column is NULL in all 1,900 recorded frames.
`StepRecord` carries it now, and the one number a threshold can be tested against is the
minimum across an episode, because `should_raise` fires strictly below the threshold — so
"would this episode have handed over at T" is exactly `lowest_confidence < T`.

**Only episodes nobody took over count.** An episode with an intervention has a trajectory
that is partly an operator's, so whether it succeeded is not an answer about the policy
alone — and a threshold is a question about the policy alone. Getting this backwards would
produce a curve that looks like calibration and measures teamwork.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tendon.services.progress import (
    EpisodeRecord,
    append,
    history,
    now,
    threshold_curve,
)


def _record(**overrides) -> EpisodeRecord:
    fields = {
        "skill": "s",
        "body": "b",
        "episode_id": "e",
        "ended_at": now(),
        "steps": 100,
        "interventions": 0,
        "corrections": 0,
        "corrections_known": 0,
    }
    fields.update(overrides)
    return EpisodeRecord(**fields)


# ------------------------------------------------------------------ the number itself


def test_the_lowest_score_across_an_episode_is_what_is_kept() -> None:
    """A chunk's score repeats across its steps, so an episode has many; the threshold
    only ever meets the smallest."""
    from tendon.kernel.scheduler import EpisodeResult, StepRecord
    from tendon.kernel.types import (
        Action,
        ActionSpace,
        Confidence,
        ConfidenceSource,
        Observation,
        Proprioception,
    )

    def step(score: float, source: ConfidenceSource) -> StepRecord:
        action = Action(space=ActionSpace.JOINT_POSITION, values=[0.0])
        return StepRecord(
            step=0,
            observation=Observation(step=0, proprio=Proprioception(joint_positions=(0.0,))),
            commanded=action,
            applied=action,
            confidence=Confidence(score=score, source=source),
        )

    result = EpisodeResult(episode_id="x")
    result.records = [
        step(0.9, ConfidenceSource.CHUNK_VARIANCE),
        step(0.2, ConfidenceSource.CHUNK_VARIANCE),
        step(0.7, ConfidenceSource.CHUNK_VARIANCE),
    ]

    assert result.lowest_confidence == pytest.approx(0.2)


def test_an_unmeasured_score_does_not_drag_the_minimum_down() -> None:
    """`ConfidenceSource.NONE` carries a default, not an observation. Counting it would put
    every episode at the bottom of every threshold comparison."""
    from tendon.kernel.scheduler import EpisodeResult, StepRecord
    from tendon.kernel.types import (
        Action,
        ActionSpace,
        Confidence,
        ConfidenceSource,
        Observation,
        Proprioception,
    )

    action = Action(space=ActionSpace.JOINT_POSITION, values=[0.0])
    observation = Observation(step=0, proprio=Proprioception(joint_positions=(0.0,)))

    result = EpisodeResult(episode_id="x")
    result.records = [
        StepRecord(
            step=0,
            observation=observation,
            commanded=action,
            applied=action,
            confidence=Confidence(score=0.0, source=ConfidenceSource.NONE),
        ),
        StepRecord(
            step=1,
            observation=observation,
            commanded=action,
            applied=action,
            confidence=Confidence(score=0.6, source=ConfidenceSource.CHUNK_VARIANCE),
        ),
    ]

    assert result.lowest_confidence == pytest.approx(0.6)


def test_an_episode_nothing_scored_has_no_lowest() -> None:
    from tendon.kernel.scheduler import EpisodeResult

    assert EpisodeResult(episode_id="x").lowest_confidence is None


# ------------------------------------------------------------------- across the disk


@pytest.mark.parametrize("score", [0.0, 0.42, 1.0, None], ids=["zero", "middling", "one", "absent"])
def test_the_score_survives_the_round_trip(tmp_path: Path, score: float | None) -> None:
    """Zero is included deliberately. It is a real measurement and the most alarming one
    available — the policy was as unsure as the scale allows — and any reader that treated
    it as absent would hide exactly the episode worth looking at."""
    append(tmp_path, "s", "b", _record(lowest_confidence=score))

    (back,) = history(tmp_path, "s", "b")

    assert back.lowest_confidence == score


def test_zero_and_absent_stay_different(tmp_path: Path) -> None:
    append(tmp_path, "s", "b", _record(episode_id="scored", lowest_confidence=0.0))
    append(tmp_path, "s", "b", _record(episode_id="not", lowest_confidence=None))

    scored, unscored = history(tmp_path, "s", "b")

    assert scored.lowest_confidence == 0.0
    assert unscored.lowest_confidence is None


# ------------------------------------------------------------------------ the curve


def test_a_threshold_asks_exactly_when_the_runtime_would() -> None:
    """Strictly below, matching `kernel.interrupt.should_raise`. A curve that disagreed
    with the runtime about when a handover happens would be calibrating something the
    system does not do."""
    records = (
        _record(episode_id="a", lowest_confidence=0.3),
        _record(episode_id="b", lowest_confidence=0.5),
    )

    (at_half,) = threshold_curve(records, thresholds=[0.5])

    assert at_half.would_ask == 1, "0.3 is below 0.5 and 0.5 is not"
    assert at_half.would_not_ask == 1


def test_an_episode_somebody_took_over_is_not_counted() -> None:
    """Its trajectory is partly an operator's, so its outcome is not an answer about the
    policy — which is the only thing a threshold is a question about."""
    records = (
        _record(episode_id="clean", lowest_confidence=0.9, succeeded=True),
        _record(episode_id="helped", lowest_confidence=0.1, succeeded=True, interventions=3),
    )

    (point,) = threshold_curve(records, thresholds=[0.5])

    assert point.would_ask == 0
    assert point.would_not_ask == 1, "only the untouched episode"


def test_an_unscored_episode_is_not_counted() -> None:
    records = (
        _record(episode_id="scored", lowest_confidence=0.9),
        _record(episode_id="not", lowest_confidence=None),
    )

    (point,) = threshold_curve(records, thresholds=[0.5])

    assert point.would_ask + point.would_not_ask == 1


def test_nothing_usable_gives_no_curve_rather_than_a_row_of_zeroes() -> None:
    """The state this machine is in: six episodes, none scored. A table of zeroes would be
    the most confident-looking wrong answer available."""
    assert threshold_curve([_record(lowest_confidence=None) for _ in range(6)]) == ()
    assert threshold_curve(()) == ()


def test_the_success_rate_is_over_the_episodes_that_would_have_run_on() -> None:
    """Not over all of them. The question is what it costs to *not* ask, so the episodes
    that would have been interrupted tell you nothing about it."""
    records = (
        _record(episode_id="a", lowest_confidence=0.1, succeeded=False),  # would ask
        _record(episode_id="b", lowest_confidence=0.8, succeeded=True),
        _record(episode_id="c", lowest_confidence=0.9, succeeded=False),
    )

    (point,) = threshold_curve(records, thresholds=[0.5])

    assert point.would_ask == 1
    assert point.judged_unasked == 2
    assert point.success_rate_unasked == pytest.approx(0.5), "b succeeded, c did not"


def test_an_unjudged_outcome_is_not_a_failed_one() -> None:
    """The same three-state rule as everywhere else. None, not zero."""
    records = (
        _record(episode_id="a", lowest_confidence=0.8, succeeded=None),
        _record(episode_id="b", lowest_confidence=0.9, succeeded=None),
    )

    (point,) = threshold_curve(records, thresholds=[0.5])

    assert point.would_not_ask == 2
    assert point.judged_unasked == 0
    assert point.success_rate_unasked is None


def test_a_higher_threshold_never_asks_less() -> None:
    """A monotonicity the shape guarantees, asserted because a comparison written the wrong
    way round still produces a plausible-looking table."""
    records = tuple(_record(episode_id=str(i), lowest_confidence=i / 10) for i in range(10))

    curve = threshold_curve(records)
    asks = [point.would_ask for point in curve]

    assert asks == sorted(asks), f"{asks} is not non-decreasing in the threshold"
    assert asks[0] < asks[-1], "and the candidates have to actually separate the episodes"
