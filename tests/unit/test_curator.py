"""Curation metrics get the strictest tests in the repository.

Everything else fails loudly. This fails quietly: a metric that mislabels good episodes as
bad removes them from training, the policy gets slightly worse, and nothing goes red. The
damage appears weeks later as a plateau nobody can explain.

So these tests cover three things, not one:

1. that each signal catches what it claims to catch
2. that each signal is honest about what it cannot see
3. that interrupt episodes survive ranking, since they score badly for the wrong reason
   and are the most valuable data in the store
"""

from __future__ import annotations

import math

import pytest

from tendon.kernel.types import Action, ActionSpace
from tendon.services.curator import (
    EpisodeSignals,
    ScoredEpisode,
    gripper_churn,
    idle_fraction,
    length_ratio,
    median_steps,
    peak_jerk,
    score_episode,
    select,
    signals_for,
)

DT = 0.02  # 50 Hz


def joints(*values: float, gripper: float | None = None) -> Action:
    return Action(space=ActionSpace.JOINT_POSITION, values=list(values), gripper=gripper)


def smooth(steps: int = 50) -> list[Action]:
    """A clean linear sweep. Zero jerk by construction."""
    return [joints(i * 0.01, 0.0) for i in range(steps)]


def stuttering(steps: int = 50) -> list[Action]:
    """The same sweep with the operator hesitating every other step."""
    return [joints((i // 2) * 0.02, 0.0) for i in range(steps)]


# -------------------------------------------------------------------------------- jerk


def test_smooth_motion_has_no_jerk() -> None:
    assert peak_jerk(smooth(), DT) == pytest.approx(0.0, abs=1e-9)


def test_stutter_produces_jerk() -> None:
    assert peak_jerk(stuttering(), DT) > peak_jerk(smooth(), DT)


def test_jerk_needs_four_points() -> None:
    """Three differentiations need four samples. Fewer is not zero jerk, it is unknown —
    reported as 0.0 and documented, since a short episode fails the length signal anyway."""
    assert peak_jerk(smooth(3), DT) == 0.0


def test_jerk_ignores_non_joint_actions() -> None:
    cartesian = [Action(space=ActionSpace.EE_ABS_POSE, values=[0, 0, 0, 0, 0, 0])] * 10
    assert peak_jerk(cartesian, DT) == 0.0


def test_jerk_rejects_nonpositive_dt() -> None:
    with pytest.raises(ValueError):
        peak_jerk(smooth(), 0.0)


def test_jerk_is_blind_to_a_smooth_wrong_motion() -> None:
    """The documented hole in this module.

    A confidently executed motion toward entirely the wrong place scores identically to a
    correct one. Catching it needs instruction-action consistency, which needs a model,
    which is v0.3 work. This test exists so the gap is asserted rather than assumed.
    """
    correct = [joints(i * 0.01, 0.0) for i in range(50)]
    wrong_direction = [joints(-i * 0.01, 0.0) for i in range(50)]
    assert peak_jerk(correct, DT) == pytest.approx(peak_jerk(wrong_direction, DT))


# -------------------------------------------------------------------------------- idle


def test_continuous_motion_is_not_idle() -> None:
    assert idle_fraction(smooth(), DT) == pytest.approx(0.0)


def test_a_frozen_episode_is_entirely_idle() -> None:
    frozen = [joints(0.5, 0.5) for _ in range(20)]
    assert idle_fraction(frozen, DT) == pytest.approx(1.0)


def test_half_idle_is_measured_as_half() -> None:
    """Move, hold, move, hold — every second transition is idle."""
    actions = [joints((i // 2) * 0.05, 0.0) for i in range(21)]
    assert idle_fraction(actions, DT) == pytest.approx(0.5, abs=0.05)


def test_idle_needs_two_points() -> None:
    assert idle_fraction(smooth(1), DT) == 0.0


# ------------------------------------------------------------------------------ churn


def test_a_single_grasp_is_not_churn() -> None:
    actions = [joints(0.0, gripper=1.0) for _ in range(25)]
    actions += [joints(0.0, gripper=0.0) for _ in range(25)]
    # One toggle across one second of data.
    assert gripper_churn(actions, DT) == pytest.approx(1.0, abs=0.01)


def test_grip_fighting_is_churn() -> None:
    fighting = [joints(0.0, gripper=float(i % 2)) for i in range(50)]
    assert gripper_churn(fighting, DT) > 10.0


def test_gripper_drift_is_not_a_toggle() -> None:
    """Slow drift across the range must not read as indecision."""
    drifting = [joints(0.0, gripper=i / 50) for i in range(50)]
    assert gripper_churn(drifting, DT) == pytest.approx(0.0)


def test_no_gripper_means_no_churn() -> None:
    assert gripper_churn(smooth(), DT) == 0.0


# ----------------------------------------------------------------------------- length


@pytest.mark.parametrize(
    ("steps", "median", "expected"),
    [(100, 100.0, 1.0), (50, 100.0, 0.5), (200, 100.0, 2.0)],
)
def test_length_ratio(steps: int, median: float, expected: float) -> None:
    assert length_ratio(steps, median) == pytest.approx(expected)


def test_length_ratio_with_no_reference_is_neutral() -> None:
    assert length_ratio(100, 0.0) == 1.0


def test_median_steps_of_nothing_is_zero() -> None:
    assert median_steps([]) == 0.0


# ---------------------------------------------------------------------------- scoring


def clean_signals(**overrides) -> EpisodeSignals:
    base = {
        "steps": 100,
        "peak_jerk": 1.0,
        "idle_fraction": 0.0,
        "gripper_churn": 0.0,
        "length_ratio": 1.0,
    }
    base.update(overrides)
    return EpisodeSignals(**base)


def test_a_clean_episode_scores_high() -> None:
    score, reasons = score_episode(clean_signals(), jerk_reference=1.0)
    assert score > 0.7
    assert reasons == ()


def test_a_bad_episode_scores_lower_than_a_clean_one() -> None:
    clean, _ = score_episode(clean_signals(), jerk_reference=1.0)
    bad, reasons = score_episode(
        clean_signals(peak_jerk=50.0, idle_fraction=0.6, gripper_churn=8.0, length_ratio=2.5),
        jerk_reference=1.0,
    )
    assert bad < clean
    assert len(reasons) >= 3


def test_score_stays_in_range_under_extremes() -> None:
    score, _ = score_episode(
        clean_signals(peak_jerk=1e9, idle_fraction=1.0, gripper_churn=1e6, length_ratio=100.0),
        jerk_reference=1.0,
    )
    assert 0.0 <= score <= 1.0


def test_jerk_is_scored_relative_not_absolute() -> None:
    """A value that is violent on a slow arm is nothing on a fast one.

    Scoring jerk against an absolute threshold would score the hardware, not the episode.
    """
    fast_arm, _ = score_episode(clean_signals(peak_jerk=100.0), jerk_reference=100.0)
    slow_arm, _ = score_episode(clean_signals(peak_jerk=1.0), jerk_reference=1.0)
    assert fast_arm == pytest.approx(slow_arm)


def test_length_deviation_is_symmetric() -> None:
    """Half length and double length are equally suspect."""
    short, _ = score_episode(clean_signals(length_ratio=0.6), jerk_reference=1.0)
    long_, _ = score_episode(clean_signals(length_ratio=1.4), jerk_reference=1.0)
    assert short == pytest.approx(long_)


def test_reasons_are_given_whenever_the_score_is_reduced() -> None:
    """A bare number invites blind trust or blind dismissal. Both are worse than an
    argument, so anything that lowers a score must say why."""
    _, reasons = score_episode(clean_signals(idle_fraction=0.55), jerk_reference=1.0)
    assert any("no motion" in r for r in reasons)


def test_zero_jerk_reference_does_not_divide() -> None:
    score, _ = score_episode(clean_signals(peak_jerk=5.0), jerk_reference=0.0)
    assert math.isfinite(score)


# -------------------------------------------------------------------- interrupt handling


def scored(episode_id: str, score: float, *, had_interrupt: bool = False) -> ScoredEpisode:
    return ScoredEpisode(
        episode_id=episode_id,
        score=score,
        signals=clean_signals(had_interrupt=had_interrupt),
        reasons=(),
    )


def test_interrupt_episodes_rank_first_despite_a_low_score() -> None:
    """The most important guarantee in this module.

    A human taking over mid-motion produces exactly the discontinuity `jerk` detects, so
    interrupt episodes score badly for the wrong reason. Ranking them by these signals
    would systematically discard the only recorded recoveries from failure.
    """
    ranked = select(
        [
            scored("clean-a", 0.95),
            scored("intervened", 0.10, had_interrupt=True),
            scored("clean-b", 0.90),
        ]
    )
    assert ranked[0].episode_id == "intervened"


def test_interrupt_priority_can_be_turned_off_explicitly() -> None:
    ranked = select(
        [scored("clean", 0.95), scored("intervened", 0.10, had_interrupt=True)],
        keep_interrupts=False,
    )
    assert ranked[0].episode_id == "clean"


def test_score_flags_an_interrupt_episode_as_not_comparable() -> None:
    _, reasons = score_episode(clean_signals(had_interrupt=True), jerk_reference=1.0)
    assert any("intervention" in r for r in reasons)


# ----------------------------------------------------------------------------- select


def test_select_ranks_but_never_filters() -> None:
    """An automated curator that is wrong about an episode is wrong permanently.

    So `select` returns an ordering. Removal stays a human decision.
    """
    episodes = [scored("a", 0.9), scored("b", 0.1), scored("c", 0.5)]
    assert len(select(episodes)) == 3


def test_select_orders_by_score() -> None:
    ranked = select([scored("low", 0.1), scored("high", 0.9), scored("mid", 0.5)])
    assert [e.episode_id for e in ranked] == ["high", "mid", "low"]


def test_select_limit_truncates_after_ranking() -> None:
    ranked = select([scored("low", 0.1), scored("high", 0.9)], limit=1)
    assert [e.episode_id for e in ranked] == ["high"]


def test_select_of_nothing_is_nothing() -> None:
    assert select([]) == []


# ------------------------------------------------------------------------- integration


def test_signals_for_measures_a_whole_episode() -> None:
    signals = signals_for(smooth(100), DT, median_steps=100.0)
    assert signals.steps == 100
    assert signals.length_ratio == pytest.approx(1.0)
    assert signals.idle_fraction == pytest.approx(0.0)
    assert not signals.had_interrupt


def test_a_stuttering_episode_scores_below_a_smooth_one() -> None:
    """End to end: the pipeline ranks a bad recording below a good one."""
    reference = peak_jerk(smooth(100), DT) or 1.0

    good = signals_for(smooth(100), DT, median_steps=100.0)
    bad = signals_for(stuttering(100), DT, median_steps=100.0)

    good_score, _ = score_episode(good, jerk_reference=max(reference, 1e-6))
    bad_score, _ = score_episode(bad, jerk_reference=max(reference, 1e-6))

    assert bad_score < good_score
