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
import random

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


# ---------------------------------------------------------------- values that lie


@pytest.mark.parametrize(
    ("state", "chunk"),
    [
        ([math.nan, 0.2], [joint(0.2, 0.2)] * HORIZON),
        ([0.2, 0.2], [joint(0.2, math.nan)] * HORIZON),
        ([0.2, math.inf], [joint(0.2, 0.2)] * HORIZON),
    ],
)
def test_a_non_finite_value_in_the_chunk_is_not_measured(state, chunk) -> None:
    """A sensor dropout makes every distance NaN, and `min(1.0, nan)` is 1.0: without the
    check it scored full confidence with this source and suppressed the handover."""
    result = estimate_from_demonstrations(index(), state, chunk)

    assert result.source is ConfidenceSource.NONE
    assert result.score == 0.0
    assert "not finite" in result.reasons[0]


@pytest.mark.parametrize("where", ["states", "actions"])
def test_a_demonstration_holding_a_non_finite_value_is_refused(where: str) -> None:
    """One NaN made the median NaN, `NaN <= 0` is False, so the index counted as scaled
    and every chunk then scored 1.0. Malformed, not unsuccessful: refused loudly."""
    states = [[0.0, 0.0]] * STEPS
    actions = [joint(0.0, 0.0)] * STEPS
    if where == "states":
        states = [*states[:-1], [0.0, math.nan]]
    else:
        actions = [*actions[:-1], joint(math.nan, 0.0)]
    broken = Demonstration(
        episode_id="dropout", states=states, actions=actions, succeeded=True, interventions=0
    )

    with pytest.raises(ValueError, match="dropout.*not finite"):
        DemonstrationIndex.build([*spaced(), broken], horizon=HORIZON)


# ------------------------------------------------------------ the scale, harder cases


def test_a_shared_home_pose_does_not_collapse_the_scale() -> None:
    """Three episodes hold the same home pose for 20 steps and then move differently. The
    home windows match another episode exactly, so more than half the nearest distances
    are zero; counted, they made the median zero and the index answered "identical".
    Dropped, as `calibration.from_spreads` drops zero spreads, the scale is what the
    moving windows say: every window that leaves home is 0.1 from its neighbour episode
    at the same step (offsets 0.0, 0.1, 0.2 on joint 0 after step 20)."""

    def leaves_home(episode_id: str, offset: float) -> Demonstration:
        states = [[0.0, 0.0]] * 20 + [[offset, 0.0]] * 10
        return Demonstration(
            episode_id=episode_id,
            states=states,
            actions=[joint(*s) for s in states],
            succeeded=True,
            interventions=0,
        )

    built = DemonstrationIndex.build(
        [leaves_home("a", 0.0), leaves_home("b", 0.1), leaves_home("c", 0.2)], horizon=HORIZON
    )

    assert built.unscaled_because is None
    # Windows that have left home: state 0.1 apart on one of two joints, motion zero.
    # Windows straddling the step out differ in motion too; the median sits in the first.
    assert built.reference_distance == pytest.approx(0.1 / math.sqrt(2))


def test_the_scale_holds_across_the_block_boundary() -> None:
    """Over 2048 windows the leave-one-out search runs in blocks. Joint 1 ramps by 0.001 a
    step, so a window's own episode is 0.0007 away and another episode 0.0707 away: a block
    that masked the wrong rows would pull the median down by a factor of a hundred."""

    def ramp(episode_id: str, offset: float) -> Demonstration:
        states = [[offset, 0.001 * t] for t in range(702)]
        return Demonstration(
            episode_id=episode_id,
            states=states,
            actions=[joint(*s) for s in states],
            succeeded=True,
            interventions=0,
        )

    built = DemonstrationIndex.build([ramp(str(k), 0.1 * k) for k in range(3)], horizon=HORIZON)

    assert built.windows == 2100
    assert built.reference_distance == pytest.approx(0.1 / math.sqrt(2))


def test_the_scale_does_not_depend_on_where_the_joints_are_zeroed() -> None:
    """Shifting every demonstration by the same amount changes no distance. Far from zero,
    |x|^2 + |y|^2 - 2xy loses the hundredths that separate neighbours to rounding and picks
    the wrong partner unless the vectors are centred first. Random walks rather than the
    spaced geometry, which has so few distinct distances that a wrong pick still lands on
    the right value."""

    def walks(shift: float) -> list[Demonstration]:
        rng = random.Random(1)
        demos = []
        for episode in range(6):
            position = [shift] * 6
            states = []
            for _ in range(40):
                position = [p + rng.gauss(0.0, 0.01) for p in position]
                states.append(position)
            demos.append(
                Demonstration(
                    episode_id=str(episode),
                    states=states,
                    actions=[joint(*(v + 0.01 for v in s)) for s in states],
                    succeeded=True,
                    interventions=0,
                )
            )
        return demos

    near_zero = DemonstrationIndex.build(walks(0.0), horizon=5).reference_distance
    far_away = DemonstrationIndex.build(walks(1e7), horizon=5).reference_distance

    assert near_zero is not None
    assert far_away == pytest.approx(near_zero, rel=1e-4)


def test_a_distance_that_overflows_is_not_a_measurement() -> None:
    """Finite inputs, infinite distance: 1e200 squared is inf. Not a number an operator
    can read against a threshold, so it is reported as unmeasured with the reason."""
    result = estimate_from_demonstrations(index(), [1e200, 1e200], [joint(1e200, 1e200)] * 3)

    assert result.source is ConfidenceSource.NONE
    assert result.reasons == ("distance from the demonstrations is not finite",)
