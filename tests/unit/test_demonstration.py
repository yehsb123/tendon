"""A second confidence source, and the numbers it has to get right.

`services/demonstration.py` scores a predicted chunk by how far it is from the nearest
successful demonstration that started from a similar state. The tests assert computed
distances and scores rather than verdicts: a guard that answers "far" for the wrong reason
passes every verdict test and is still wrong.

Built on a geometry where every distance can be worked out by hand. Each demonstration
holds the arm still at one offset, so a window's start state is that offset and its motion
is zero; the distance between two such windows is the difference in offsets.
"""

from __future__ import annotations

import math

import pytest

from tendon.kernel.types import Action, ActionSpace, ConfidenceSource
from tendon.services.demonstration import (
    MINIMUM_WINDOWS,
    Demonstration,
    DemonstrationIndex,
    estimate_from_demonstrations,
)

DOF = 2
HORIZON = 3
STEPS = 10  # 8 windows per demonstration at HORIZON 3


def joint(*values: float) -> Action:
    return Action(space=ActionSpace.JOINT_POSITION, values=list(values))


def still(
    episode_id: str,
    offset: float,
    *,
    succeeded: bool | None = True,
    interventions: int | None = 0,
) -> Demonstration:
    """An arm held at `offset` on every joint. Start state = offset, motion = 0."""
    return Demonstration(
        episode_id=episode_id,
        states=[[offset] * DOF for _ in range(STEPS)],
        actions=[joint(*([offset] * DOF)) for _ in range(STEPS)],
        succeeded=succeeded,
        interventions=interventions,
    )


def spaced() -> list[Demonstration]:
    """Four demonstrations 0.1 apart and one far outlier.

    Every window's nearest window in *another* episode is 0.1 away, except the outlier's,
    which is 4.7 away. Five episodes of eight windows: the median is 0.1 and the mean is
    about 1.02, so a mean-based scale and a median-based one give different answers here.
    """
    return [still(str(i), 0.1 * i) for i in range(4)] + [still("outlier", 5.0)]


def index() -> DemonstrationIndex:
    return DemonstrationIndex.build(spaced(), horizon=HORIZON)


# ------------------------------------------------------------------------ the scale


def test_the_reference_is_the_median_nearest_distance_to_another_episode() -> None:
    """Leave-one-episode-out. If a window could match its own episode the nearest distance
    would be zero for every window and there would be no scale at all."""
    built = index()

    assert built.windows == 5 * (STEPS - HORIZON + 1)
    assert built.reference_distance == pytest.approx(0.1)


def test_a_new_demonstration_spaced_like_the_others_scores_one_half() -> None:
    """What "typical" has to mean for the number to be readable, as in calibration.py."""
    result = estimate_from_demonstrations(index(), [0.4, 0.4], [joint(0.4, 0.4)] * HORIZON)

    assert result.source is ConfidenceSource.DEMONSTRATION
    assert result.score == pytest.approx(0.5)


def test_a_chunk_identical_to_a_demonstration_scores_one() -> None:
    result = estimate_from_demonstrations(index(), [0.2, 0.2], [joint(0.2, 0.2)] * HORIZON)

    assert result.score == pytest.approx(1.0)
    assert result.reasons == ()


def test_a_chunk_far_from_every_demonstration_scores_by_its_distance() -> None:
    """From the state of demonstration 0, move 0.9 on each joint at every step. The state
    part is zero and the motion part is 0.9 RMS, so the distance is 0.9 and the score is
    1 / (1 + 0.9 / 0.1) = 0.1."""
    built = index()
    nearest = built.nearest([0.0, 0.0], [joint(0.9, 0.9)] * HORIZON)
    assert nearest is not None
    assert nearest.state_part == pytest.approx(0.0)
    assert nearest.motion_part == pytest.approx(0.9)
    assert nearest.distance == pytest.approx(0.9)

    result = estimate_from_demonstrations(built, [0.0, 0.0], [joint(0.9, 0.9)] * HORIZON)

    assert result.score == pytest.approx(0.1)
    assert any("9.0x" in reason for reason in result.reasons)
    assert any("moved differently" in reason for reason in result.reasons)


def test_only_the_first_horizon_steps_are_compared() -> None:
    """The index holds windows of `horizon` steps; a longer chunk's tail has nothing to
    be compared against and must not move the score."""
    tail = [joint(0.2, 0.2)] * HORIZON + [joint(9.0, 9.0)] * 4
    result = estimate_from_demonstrations(index(), [0.2, 0.2], tail)

    assert result.score == pytest.approx(1.0)


# ------------------------------------------------------------- conditioning on state


def test_the_same_chunk_from_a_different_start_state_is_a_different_motion() -> None:
    """Demonstration 0 holds still at 0.0. The same absolute chunk issued with the arm at
    0.05 is a move of -0.05 from there: state part 0.05, motion part 0.05, distance
    0.05 * sqrt(2) against demonstration 0 — and demonstration 1 at 0.1 is no closer."""
    chunk = [joint(0.0, 0.0)] * HORIZON
    built = index()

    at_home = estimate_from_demonstrations(built, [0.0, 0.0], chunk)
    elsewhere = built.nearest([0.05, 0.05], chunk)
    assert elsewhere is not None

    assert at_home.score == pytest.approx(1.0)
    assert elsewhere.state_part == pytest.approx(0.05)
    assert elsewhere.motion_part == pytest.approx(0.05)
    assert elsewhere.distance == pytest.approx(0.05 * math.sqrt(2))
    assert estimate_from_demonstrations(built, [0.05, 0.05], chunk).score == pytest.approx(
        1.0 / (1.0 + 0.05 * math.sqrt(2) / 0.1)
    )


def test_a_state_no_demonstration_started_from_is_named_as_such() -> None:
    """Holding still at 1.0: no motion difference, 0.7 of state difference from the
    nearest (0.3 is the closest of the spaced four; the outlier is 4.0 away)."""
    result = estimate_from_demonstrations(index(), [1.0, 1.0], [joint(1.0, 1.0)] * HORIZON)

    assert result.score == pytest.approx(1.0 / (1.0 + 0.7 / 0.1))
    assert any("no successful demonstration started near" in r for r in result.reasons)


def test_the_nearest_demonstration_is_reported_by_episode_and_step() -> None:
    built = DemonstrationIndex.build(
        [
            *spaced(),
            Demonstration(
                episode_id="ramp",
                states=[[0.0, 0.0]] * 5 + [[2.0, 2.0]] * 5,
                actions=[joint(0.0, 0.0)] * 5 + [joint(2.5, 2.5)] * 5,
                succeeded=True,
                interventions=0,
            ),
        ],
        horizon=HORIZON,
    )

    nearest = built.nearest([2.0, 2.0], [joint(2.5, 2.5)] * HORIZON)

    assert nearest is not None
    assert nearest.episode_id == "ramp"
    assert nearest.step == 5
    assert nearest.distance == pytest.approx(0.0)


# ------------------------------------------------------------ which demonstrations


def test_only_unaided_successes_are_indexed() -> None:
    """`succeeded` is the evaluator's verdict and `interventions` the scheduler's count,
    the same two fields `progress.EpisodeRecord` carries. Unknown is not success, and an
    episode somebody took over is not the policy's or a demonstrator's own motion."""
    built = DemonstrationIndex.build(
        [
            *spaced(),
            still("failed", 3.0, succeeded=False),
            still("unjudged", 3.0, succeeded=None),
            still("taken-over", 3.0, interventions=2),
            still("uncounted", 3.0, interventions=None),
        ],
        horizon=HORIZON,
    )

    assert built.episodes == ("0", "1", "2", "3", "outlier")
    assert dict(built.excluded) == {
        "failed": "did not succeed",
        "unjudged": "success was not measured",
        "taken-over": "an operator took over 2 time(s)",
        "uncounted": "whether an operator took over is not known",
    }
    # Matching an excluded episode exactly earns nothing: it is not in the index.
    result = estimate_from_demonstrations(built, [3.0, 3.0], [joint(3.0, 3.0)] * HORIZON)
    assert result.score == pytest.approx(1.0 / (1.0 + 2.0 / 0.1))


# ------------------------------------------------------------- refusing to measure


def test_an_empty_index_answers_none_with_a_reason() -> None:
    built = DemonstrationIndex.build([], horizon=HORIZON)
    result = estimate_from_demonstrations(built, [0.0, 0.0], [joint(0.0, 0.0)] * HORIZON)

    assert built.reference_distance is None
    assert result.source is ConfidenceSource.NONE
    assert result.score == 0.0
    assert result.reasons == ("no successful demonstrations are indexed",)


def test_one_episode_has_no_other_episode_to_be_compared_with() -> None:
    built = DemonstrationIndex.build([still("only", 0.0)], horizon=HORIZON)
    result = estimate_from_demonstrations(built, [0.0, 0.0], [joint(0.0, 0.0)] * HORIZON)

    assert result.source is ConfidenceSource.NONE
    assert "at least 2" in result.reasons[0]


def test_too_few_windows_is_refused_rather_than_given_a_median() -> None:
    """Two episodes of HORIZON + 1 steps give four windows, well under MINIMUM_WINDOWS."""
    short = [
        Demonstration(
            episode_id=str(i),
            states=[[0.1 * i] * DOF] * (HORIZON + 1),
            actions=[joint(*([0.1 * i] * DOF))] * (HORIZON + 1),
            succeeded=True,
            interventions=0,
        )
        for i in range(2)
    ]
    built = DemonstrationIndex.build(short, horizon=HORIZON)
    result = estimate_from_demonstrations(built, [0.0, 0.0], [joint(0.0, 0.0)] * HORIZON)

    assert built.windows == 4 < MINIMUM_WINDOWS
    assert result.source is ConfidenceSource.NONE
    assert f"at least {MINIMUM_WINDOWS}" in result.reasons[0]


def test_identical_demonstrations_have_no_scale() -> None:
    built = DemonstrationIndex.build([still(str(i), 0.0) for i in range(4)], horizon=HORIZON)
    result = estimate_from_demonstrations(built, [0.0, 0.0], [joint(0.0, 0.0)] * HORIZON)

    assert built.reference_distance is None
    assert result.source is ConfidenceSource.NONE


def test_a_chunk_shorter_than_the_horizon_is_not_measured() -> None:
    result = estimate_from_demonstrations(index(), [0.0, 0.0], [joint(0.0, 0.0)])

    assert result.source is ConfidenceSource.NONE
    assert "1 step(s)" in result.reasons[0]


def test_a_chunk_in_another_action_space_is_not_measured() -> None:
    """Start state and motion are compared in joint space. A pose command is in metres
    and would be subtracted from joint angles."""
    pose = [Action(space=ActionSpace.EE_DELTA_POSE, values=[0.0] * 6)] * HORIZON
    result = estimate_from_demonstrations(index(), [0.0, 0.0], pose)

    assert result.source is ConfidenceSource.NONE


def test_a_chunk_of_another_width_is_not_measured() -> None:
    result = estimate_from_demonstrations(index(), [0.0, 0.0, 0.0], [joint(0, 0, 0)] * HORIZON)

    assert result.source is ConfidenceSource.NONE
    assert "3" in result.reasons[0] and "2" in result.reasons[0]


def test_a_demonstration_whose_states_and_actions_disagree_in_length_is_refused() -> None:
    broken = Demonstration(
        episode_id="broken",
        states=[[0.0, 0.0]] * 4,
        actions=[joint(0.0, 0.0)] * 5,
        succeeded=True,
        interventions=0,
    )
    with pytest.raises(ValueError, match="broken"):
        DemonstrationIndex.build([broken], horizon=HORIZON)


def test_an_empty_or_short_successful_episode_adds_no_windows() -> None:
    """Admitted, because it succeeded unaided, and contributes nothing: there is no window
    of `horizon` steps in it to compare with."""
    empty = Demonstration(
        episode_id="empty", states=[], actions=[], succeeded=True, interventions=0
    )
    short = Demonstration(
        episode_id="short",
        states=[[0.0, 0.0]] * (HORIZON - 1),
        actions=[joint(0.0, 0.0)] * (HORIZON - 1),
        succeeded=True,
        interventions=0,
    )
    built = DemonstrationIndex.build([empty, short, *spaced()], horizon=HORIZON)

    assert built.episodes == ("empty", "short", "0", "1", "2", "3", "outlier")
    assert built.windows == 5 * (STEPS - HORIZON + 1)
    assert built.reference_distance == pytest.approx(0.1)
