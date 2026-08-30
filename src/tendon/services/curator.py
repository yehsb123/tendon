"""Which episodes are worth training on.

One of the four things tendon writes itself, and the hardest. Collecting is easy — the
recorder is a few hundred lines. Deciding which of the three hundred episodes recorded
today should shape tomorrow's policy is an open problem nobody has agreed on.

## Why this module carries the burden of proof

Everything else in the runtime fails loudly. This fails quietly. A metric that mislabels
good episodes as bad removes them from training, the policy gets slightly worse, and no
test anywhere goes red. The damage shows up weeks later as a plateau nobody can explain.

So two rules apply here and nowhere else:

1. **Every signal states what it cannot see.** A signal that looks authoritative and is
   blind to a whole class of problem is worse than no signal.
2. **Nothing is discarded, only ranked.** `select` returns an ordering. Deletion is a
   human decision, because an automated curator that is wrong about an episode is wrong
   about it permanently.

## The signals

All are cheap, all are falsifiable, none needs a model.

**`jerk`** catches teleoperation stutter, dropped frames and control fighting.
Blind to a smoothly executed wrong motion.

**`idle_fraction`** catches operator hesitation, waiting, and a recording that kept
running after the task ended. Blind to pauses that are part of the task.

**`gripper_churn`** catches grip fighting and indecision at contact.
Blind to a task that legitimately regrasps.

**`length_ratio`** catches truncated and runaway runs.
Blind to a wrong-length episode that happens to sit near the median.

Instruction-action consistency is the signal that would catch a smoothly executed wrong
motion, and it needs a model. That is v0.3 work, and its absence is the largest known hole
in this module.

## Interrupt episodes

Kept by default and scored separately. They are the only recorded instances of recovering
from failure, which demonstration data almost never contains — and they score badly on
every signal above, because a human taking over mid-motion produces exactly the
discontinuity `jerk` is built to detect. Ranking them by these signals would systematically
discard the most valuable data in the store.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from tendon.kernel.types import Action, ActionSpace

__all__ = [
    "EpisodeSignals",
    "ScoredEpisode",
    "SignalWeights",
    "gripper_churn",
    "idle_fraction",
    "length_ratio",
    "peak_jerk",
    "score_episode",
    "select",
    "signals_for",
]

#: Below this, a step counts as no motion. Chosen as roughly the resolution of a hobby
#: servo, and deliberately a module constant rather than a magic number: it is a claim
#: about hardware, and different hardware will want a different value.
_MOTION_EPSILON_RAD = 1e-3

#: A gripper command is a toggle when it crosses this, not when it merely drifts.
_GRIPPER_TOGGLE_DELTA = 0.4


@dataclass(frozen=True)
class EpisodeSignals:
    """Raw measurements. Not a judgement — `score_episode` turns these into one."""

    steps: int
    #: Largest third derivative of joint position [rad/s^3]. Scale-dependent, so only
    #: meaningful compared against other episodes of the same skill on the same body.
    peak_jerk: float
    #: Fraction of steps with no meaningful motion, 0 to 1.
    idle_fraction: float
    #: Gripper toggles per second [1/s].
    gripper_churn: float
    #: Length relative to the median for this skill. 1.0 is typical.
    length_ratio: float
    #: True when a human took over at any point during this episode.
    had_interrupt: bool = False


@dataclass(frozen=True)
class SignalWeights:
    """How much each signal counts.

    Defaults are a starting point, not a recommendation, and they are exposed rather than
    buried so that a site can argue with them. Nobody knows the right values yet; claiming
    otherwise would be the kind of false authority this module is supposed to avoid.
    """

    jerk: float = 0.35
    idle: float = 0.25
    churn: float = 0.15
    length: float = 0.25


@dataclass(frozen=True)
class ScoredEpisode:
    episode_id: str
    #: 0 to 1, higher is more worth training on.
    score: float
    signals: EpisodeSignals
    #: Human-readable reasons the score is what it is. Shown in the shell, because a bare
    #: number gives a reviewer nothing to disagree with.
    reasons: tuple[str, ...]


# ----------------------------------------------------------------------------- signals


def peak_jerk(actions: Sequence[Action], dt_s: float) -> float:
    """Largest magnitude of the third derivative of commanded joint position.

    Catches stutter: teleoperation hesitation, dropped frames, a controller fighting
    itself. Blind to a smooth motion toward the wrong place, which is the failure mode
    that needs a model to detect.

    Returns 0.0 when there are too few steps to differentiate three times.
    """
    if dt_s <= 0.0:
        raise ValueError(f"dt_s must be positive, got {dt_s}")

    positions = [a.values for a in actions if a.space is ActionSpace.JOINT_POSITION]
    if len(positions) < 4:
        return 0.0

    width = min(len(p) for p in positions)
    peak = 0.0
    for joint in range(width):
        series = [p[joint] for p in positions]
        d1 = [(b - a) / dt_s for a, b in zip(series, series[1:], strict=False)]
        d2 = [(b - a) / dt_s for a, b in zip(d1, d1[1:], strict=False)]
        d3 = [(b - a) / dt_s for a, b in zip(d2, d2[1:], strict=False)]
        peak = max(peak, max((abs(v) for v in d3), default=0.0))
    return peak


def idle_fraction(actions: Sequence[Action], dt_s: float) -> float:
    """Fraction of steps commanding no meaningful motion.

    Catches hesitation, waiting on something, a recording that kept running after the task
    ended. Blind to pauses that are part of the task — waiting for a part to settle is
    indistinguishable from waiting for the operator to decide, from here.
    """
    if dt_s <= 0.0:
        raise ValueError(f"dt_s must be positive, got {dt_s}")

    positions = [a.values for a in actions if a.space is ActionSpace.JOINT_POSITION]
    if len(positions) < 2:
        return 0.0

    idle = 0
    for previous, current in zip(positions, positions[1:], strict=False):
        delta = max(
            (abs(b - a) for a, b in zip(previous, current, strict=False)),
            default=0.0,
        )
        if delta < _MOTION_EPSILON_RAD:
            idle += 1
    return idle / (len(positions) - 1)


def gripper_churn(actions: Sequence[Action], dt_s: float) -> float:
    """Gripper toggles per second.

    Catches grip fighting and indecision at contact, which is where manipulation episodes
    usually go wrong. Blind to tasks that legitimately regrasp — a skill involving
    repeated pick-and-place will look churny and is not.
    """
    if dt_s <= 0.0:
        raise ValueError(f"dt_s must be positive, got {dt_s}")

    values = [a.gripper for a in actions if a.gripper is not None]
    if len(values) < 2:
        return 0.0

    toggles = sum(
        1 for a, b in zip(values, values[1:], strict=False) if abs(b - a) >= _GRIPPER_TOGGLE_DELTA
    )
    duration = len(values) * dt_s
    return toggles / duration if duration > 0 else 0.0


def length_ratio(steps: int, median_steps: float) -> float:
    """Episode length relative to the median for this skill.

    Catches truncated and runaway runs. Blind to a wrong-length episode that happens to
    land near the median, which is most of them.
    """
    if median_steps <= 0:
        return 1.0
    return steps / median_steps


def signals_for(
    actions: Sequence[Action],
    dt_s: float,
    median_steps: float,
    *,
    had_interrupt: bool = False,
) -> EpisodeSignals:
    """Measure one episode."""
    return EpisodeSignals(
        steps=len(actions),
        peak_jerk=peak_jerk(actions, dt_s),
        idle_fraction=idle_fraction(actions, dt_s),
        gripper_churn=gripper_churn(actions, dt_s),
        length_ratio=length_ratio(len(actions), median_steps),
        had_interrupt=had_interrupt,
    )


# ------------------------------------------------------------------------------ scoring


def score_episode(
    signals: EpisodeSignals,
    *,
    jerk_reference: float,
    weights: SignalWeights | None = None,
) -> tuple[float, tuple[str, ...]]:
    """Turn measurements into a score in [0, 1], with reasons.

    `jerk_reference` is the population scale — typically the median peak jerk across
    episodes of the same skill on the same body. Jerk has no absolute meaning: a value
    that is violent on a 6-axis arm is nothing on a fast delta robot, so scoring against
    an absolute threshold would be scoring the hardware rather than the episode.

    Returns the score and the reasons behind it. The reasons exist so a reviewer has
    something to disagree with; a bare number invites either blind trust or blind
    dismissal, and both are worse than an argument.
    """
    w = weights or SignalWeights()
    reasons: list[str] = []

    # Each term is 1.0 when ideal and falls toward 0.0 as the signal worsens. Bounded
    # rather than linear, so one extreme signal cannot dominate the whole score.
    if jerk_reference > 0:
        jerk_term = 1.0 / (1.0 + signals.peak_jerk / jerk_reference)
        if signals.peak_jerk > jerk_reference * 3:
            reasons.append(
                f"jerk {signals.peak_jerk:.1f} is {signals.peak_jerk / jerk_reference:.1f}x "
                "the median for this skill"
            )
    else:
        jerk_term = 1.0

    idle_term = 1.0 - signals.idle_fraction
    if signals.idle_fraction > 0.3:
        reasons.append(f"{signals.idle_fraction:.0%} of steps command no motion")

    churn_term = 1.0 / (1.0 + signals.gripper_churn)
    if signals.gripper_churn > 1.0:
        reasons.append(f"gripper toggles {signals.gripper_churn:.1f} times per second")

    # Symmetric around 1.0: half length and double length are equally suspect.
    deviation = abs(signals.length_ratio - 1.0)
    length_term = max(0.0, 1.0 - deviation)
    if deviation > 0.5:
        reasons.append(f"length is {signals.length_ratio:.1f}x the median")

    total_weight = w.jerk + w.idle + w.churn + w.length
    score = (
        w.jerk * jerk_term + w.idle * idle_term + w.churn * churn_term + w.length * length_term
    ) / total_weight

    if signals.had_interrupt:
        # Not a bonus applied to a comparable number — a statement that this episode is
        # not comparable. A human taking over mid-motion produces exactly the
        # discontinuity `jerk` detects, so these episodes score badly for the wrong
        # reason, and ranking them alongside the rest would discard the recoveries.
        reasons.append(
            "contains a human intervention - the only recorded recovery from failure, "
            "and scored separately for that reason"
        )

    return max(0.0, min(1.0, score)), tuple(reasons)


def select(
    scored: Sequence[ScoredEpisode],
    *,
    limit: int | None = None,
    keep_interrupts: bool = True,
) -> list[ScoredEpisode]:
    """Rank episodes, best first.

    Never deletes and never filters by threshold. An automated curator that is wrong about
    an episode is wrong about it permanently, so this returns an ordering and leaves
    removal to a human.

    Interrupt episodes are placed first when `keep_interrupts` is set, regardless of their
    scores, because their scores measure the wrong thing.
    """
    interrupts = [s for s in scored if s.signals.had_interrupt]
    ordinary = sorted(
        (s for s in scored if not s.signals.had_interrupt),
        key=lambda s: s.score,
        reverse=True,
    )

    ranked = (
        [*interrupts, *ordinary]
        if keep_interrupts
        else sorted(scored, key=lambda s: s.score, reverse=True)
    )

    return ranked[:limit] if limit is not None else ranked


def median_steps(episodes: Sequence[int]) -> float:
    """Median episode length, for use as the `length_ratio` reference."""
    return statistics.median(episodes) if episodes else 0.0
