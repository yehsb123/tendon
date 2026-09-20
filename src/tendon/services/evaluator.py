"""Run a skill against its evaluation set and report what happened.

Two outputs. Success rate is the number people ask for. The failure mode breakdown is the
one that changes what you do next, and it is what the shell shows.

This module also produces the graph that decides the project: cumulative human corrections
on x, intervention rate on y. See `docs/roadmap.md`, v0.3.

## Why this module is written defensively

It reports the number the project is judged on. That creates a specific pressure — every
convenient rounding, every dropped outlier, every quietly excluded episode moves the result
in the flattering direction, and none of it looks like misconduct from the inside.

So three rules:

1. **Faults are excluded and counted.** An interrupt whose context could not support a
   resume is not an intervention (`InterruptState.FAULTED`), but it is also not nothing.
   Dropping faults silently would make a system that crashes often look like one that
   needs little help.
2. **Small samples are labelled, not smoothed.** An intervention rate from nine episodes
   is reported with the fact that it came from nine episodes attached, and
   `is_significant` refuses to call a difference real below a threshold.
3. **The estimator is part of the result.** A rate measured under `chunk_variance` is not
   comparable to one under a learned head (ADR 0003). Publishing them on one axis would be
   a false result, so the source travels with the number.

## What this cannot tell you

Whether the policy got better. A falling intervention rate is consistent with a policy that
improved, and equally with an operator who got tired of being asked. Distinguishing them
needs the success rate to hold or rise at the same time, which is why both are always
reported together and why `InterventionPoint` carries both.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from tendon.kernel.types import ConfidenceSource

__all__ = [
    "EpisodeOutcome",
    "SuccessCriterion",
    "judge",
    "judge_result",
    "EvaluationResult",
    "InterventionPoint",
    "evaluate",
    "intervention_curve",
    "is_significant",
]

#: Below this many episodes, a rate is reported but never called a difference.
_MIN_EPISODES_FOR_SIGNIFICANCE = 30


@dataclass(frozen=True)
class EpisodeOutcome:
    """One evaluated episode, flattened to what the report needs.

    Deliberately not `EpisodeResult` from the scheduler: evaluation also covers episodes
    replayed from storage, which never ran through a scheduler in this process.
    """

    episode_id: str
    skill: str
    #: Whether the skill's success conditions held, or None when nobody could tell.
    #:
    #: Three states, because "failed" and "unmeasured" are opposite claims. This was
    #: `bool`, and the caller filled it with `bool(verdict)` — which turns None into False.
    #: Every episode this project could produce was unjudged until the MuJoCo driver began
    #: reporting `cube_height`, so `success_rate` read 0% everywhere and said *the policy
    #: fails every time* when the truth was *nobody measured*.
    succeeded: bool | None
    #: Interrupts a human actually resolved during this episode.
    interventions: int = 0
    #: Of those, the ones supplying a replacement intent.
    corrections: int = 0
    #: True when an interrupt could not preserve enough context to resume. Counted
    #: separately and never as an intervention.
    faulted: bool = False
    #: Label for why it failed, when it failed. Free text; the breakdown groups on it.
    failure_mode: str | None = None
    #: Which estimator produced the confidence that drove handovers here.
    confidence_source: ConfidenceSource = ConfidenceSource.NONE


@dataclass(frozen=True)
class EvaluationResult:
    skill: str
    episodes: int
    successes: int
    #: Episodes where a human was asked at least once, over total episodes.
    intervention_rate: float
    #: Total interventions over total episodes. Can exceed 1.0 — an episode may hand over
    #: several times, and an operator asked five times in one run is a different situation
    #: from five runs asking once.
    interventions_per_episode: float
    corrections: int
    #: Episodes ending in a fault. Excluded from intervention counts, reported here.
    faults: int
    #: Episodes nobody could judge — the skill declares no criteria, or the body does not
    #: report the quantity they need. Neither successes nor failures.
    unjudged: int = 0
    failure_modes: dict[str, int] = field(default_factory=dict)
    #: Estimators seen across the evaluated episodes. More than one means the rates are
    #: not comparable and the result says so.
    confidence_sources: tuple[ConfidenceSource, ...] = ()
    #: Anything a reader needs to know before quoting these numbers.
    caveats: tuple[str, ...] = ()

    @property
    def judged(self) -> int:
        """Episodes a verdict could be reached on."""
        return self.episodes - self.unjudged

    @property
    def success_rate(self) -> float | None:
        """Successes over *judged* episodes, or None when none were.

        Divided by `episodes` until now, which counted every unmeasurable episode as a
        failure. That was every episode this project could produce until the MuJoCo driver
        began reporting `cube_height`, so the number read 0% and said *the policy fails
        every time* where the truth was *nobody measured*.

        None rather than 0.0 for the same reason the field behind it is three-state: a rate
        of zero is a measurement, and having none is not.
        """
        return self.successes / self.judged if self.judged else None

    @property
    def is_comparable(self) -> bool:
        """Whether this result can be put on an axis beside another.

        False when episodes were measured under different confidence estimators, or under
        none at all — in which case handovers were not policy-initiated and the rate is
        measuring something else entirely.
        """
        measured = [s for s in self.confidence_sources if s is not ConfidenceSource.NONE]
        return len(set(self.confidence_sources)) == 1 and len(measured) == 1


@dataclass(frozen=True)
class InterventionPoint:
    """One point on the graph the project lives on.

    Carries the success rate alongside, because a falling intervention rate on its own is
    consistent with a policy that improved *and* with an operator who stopped being asked
    for the wrong reasons.
    """

    cumulative_corrections: int
    #: Over every unfaulted episode in the window.
    intervention_rate: float
    #: Over the *judged* ones only, or None when none of them could be judged.
    #:
    #: A separate denominator from `intervention_rate` on purpose. Whether somebody was
    #: asked is answerable for every episode; whether the task was done is not, and folding
    #: the unanswerable ones in as failures reports a rig that cannot measure as a policy
    #: that cannot work.
    success_rate: float | None
    #: Episodes the intervention rate is over.
    episodes: int
    #: Of those, how many could be judged. `episodes` and `judged` differing is the signal
    #: that the second rate rests on less than the first.
    judged: int = 0


@dataclass(frozen=True)
class SuccessCriterion:
    """One condition a skill declares as success.

    Read from `skill.yaml`, checked against `Observation.extra` at the end of an episode.
    `cube_height_above: 0.1` becomes `SuccessCriterion("cube_height", 0.1, "above")`.

    The value comes from the driver, because only the driver knows what the scene
    contains. That keeps task-specific knowledge out of the kernel — a skill names a
    quantity, a body supplies it, and neither has to know about the other.
    """

    key: str
    threshold: float
    comparison: str = "above"

    @classmethod
    def parse(cls, name: str, threshold: float) -> SuccessCriterion:
        """Turn a `skill.yaml` key into a criterion.

        `<key>_above` and `<key>_below` are the two supported forms. A bare key defaults
        to `above`, which is the common case and the one a reader assumes.
        """
        for suffix, comparison in (("_above", "above"), ("_below", "below")):
            if name.endswith(suffix):
                return cls(name[: -len(suffix)], float(threshold), comparison)
        return cls(name, float(threshold), "above")

    def met_by(self, extra: dict[str, Any]) -> bool | None:
        """Whether this held. None when the body did not report the quantity.

        None is not failure. A skill asking about cube height on a body that does not
        report it has not failed the task — nobody measured, and recording that as a
        failure would make an unmeasurable setup look like a broken policy.
        """
        if self.key not in extra:
            return None
        try:
            value = float(extra[self.key])
        except (TypeError, ValueError):
            return None
        return value > self.threshold if self.comparison == "above" else value < self.threshold


def judge_result(loaded, result) -> bool | None:
    """Whether a finished episode met the skill's success conditions, or None.

    One function because three callers need the answer and each would otherwise write the
    same four lines: `tendon run`, `tendon eval`, and the API's session — and the API's
    did not write them at all, so an episode started from the shell landed on the v0.3
    graph with no verdict while one started from the command line landed with one. Two
    kinds of point on the same axis.

    Reads `result.final_world`, which the scheduler collects from a body that can see the
    world and never shows the policy. Not the final observation: ground truth a policy can
    read is ground truth it can learn to use, and that works in simulation and fails on
    hardware that has none.

    None when the skill declares no criteria, or when the body cannot report the quantity
    they need. Not False — those are opposite claims about what happened.
    """
    criteria = [SuccessCriterion.parse(name, value) for name, value in loaded.success_criteria]
    if not criteria:
        return None

    verdict, _ = judge(getattr(result, "final_world", {}) or {}, criteria)
    return verdict


def judge(
    final_extra: dict[str, Any], criteria: Sequence[SuccessCriterion]
) -> tuple[bool | None, str | None]:
    """Did the episode succeed, and if not, why.

    Returns `(None, reason)` when any criterion could not be evaluated: a partial verdict
    is worse than none, because it would be counted as a real result.

    All criteria must hold. The failure label names the first that did not, which is what
    the failure-mode breakdown groups on.
    """
    if not criteria:
        return None, "skill declares no success criteria"

    for criterion in criteria:
        met = criterion.met_by(final_extra)
        if met is None:
            return None, f"body does not report {criterion.key!r}"
        if not met:
            return False, f"{criterion.key} not {criterion.comparison} {criterion.threshold:g}"

    return True, None


def evaluate(outcomes: Sequence[EpisodeOutcome], *, skill: str) -> EvaluationResult:
    """Aggregate evaluated episodes into a report.

    Raises `ValueError` on an empty set rather than reporting zeroes: a success rate of
    0.0 from no episodes and a success rate of 0.0 from fifty are opposite findings.
    """
    if not outcomes:
        raise ValueError("cannot evaluate an empty set of episodes")

    mismatched = [o.episode_id for o in outcomes if o.skill != skill]
    if mismatched:
        raise ValueError(
            f"episodes from another skill in the set: {mismatched[:3]}"
            + (" and more" if len(mismatched) > 3 else "")
        )

    episodes = len(outcomes)
    successes = sum(1 for o in outcomes if o.succeeded)
    unjudged = sum(1 for o in outcomes if o.succeeded is None)
    faults = sum(1 for o in outcomes if o.faulted)

    # Faulted episodes contribute no interventions by construction — the machine never
    # reached PENDING, so nothing was resolved. Counted separately above.
    intervened = sum(1 for o in outcomes if not o.faulted and o.interventions > 0)
    total_interventions = sum(o.interventions for o in outcomes if not o.faulted)
    corrections = sum(o.corrections for o in outcomes if not o.faulted)

    failure_modes: dict[str, int] = {}
    for outcome in outcomes:
        # `is not False`, not truthiness: an unjudged episode is not a failure mode. It was
        # grouped as one, under whatever reason `judge` gave for being unable to decide —
        # so "body does not report 'cube_height'" appeared in a table headed *failure
        # modes*, which reads as the policy failing that way.
        if outcome.succeeded is not False:
            continue
        label = outcome.failure_mode or "unlabelled"
        failure_modes[label] = failure_modes.get(label, 0) + 1

    sources = tuple(sorted({o.confidence_source for o in outcomes}, key=lambda s: s.value))

    caveats: list[str] = []
    if episodes < _MIN_EPISODES_FOR_SIGNIFICANCE:
        caveats.append(
            f"{episodes} episodes is below the {_MIN_EPISODES_FOR_SIGNIFICANCE} needed "
            "to call a difference real; report the rate, do not compare it"
        )
    if faults:
        caveats.append(
            f"{faults} episode(s) faulted - an interrupt could not preserve enough "
            "context to resume. Excluded from intervention counts but not from the "
            "episode total"
        )
    if len(sources) > 1:
        caveats.append(
            "episodes were measured under more than one confidence estimator "
            f"({', '.join(s.value for s in sources)}), so these rates are not comparable"
        )
    elif sources == (ConfidenceSource.NONE,):
        caveats.append(
            "no confidence estimator was active, so any handover here was "
            "operator-initiated or a safety trip - not the policy raising its own hand"
        )
    unlabelled = failure_modes.get("unlabelled", 0)
    if unlabelled:
        caveats.append(
            f"{unlabelled} failure(s) have no mode label, so the breakdown is incomplete"
        )

    return EvaluationResult(
        skill=skill,
        episodes=episodes,
        successes=successes,
        unjudged=unjudged,
        intervention_rate=intervened / episodes,
        interventions_per_episode=total_interventions / episodes,
        corrections=corrections,
        faults=faults,
        failure_modes=dict(sorted(failure_modes.items(), key=lambda kv: -kv[1])),
        confidence_sources=sources,
        caveats=tuple(caveats),
    )


def intervention_curve(
    outcomes: Sequence[EpisodeOutcome],
    *,
    window: int = 20,
) -> list[InterventionPoint]:
    """The graph the project lives on, in chronological order.

    `outcomes` must already be ordered oldest first — this cannot sort them, since
    `EpisodeOutcome` carries no timestamp, and inferring an order from episode ids would
    silently invent one.

    The rate is computed over a trailing window rather than cumulatively. A cumulative
    rate is dominated by early episodes and keeps falling even after improvement stops,
    which would make the line look right for the wrong reason — the exact way this graph
    could lie.

    Returns nothing until a full window exists. A partial window would show a rate that
    swings wildly on the first few episodes and then settles, which reads as improvement.
    """
    if window < 2:
        raise ValueError(f"window must be at least 2, got {window}")
    if len(outcomes) < window:
        return []

    points: list[InterventionPoint] = []
    cumulative_corrections = 0

    for index, outcome in enumerate(outcomes):
        cumulative_corrections += outcome.corrections
        if index + 1 < window:
            continue

        recent = outcomes[index + 1 - window : index + 1]
        usable = [o for o in recent if not o.faulted]
        if not usable:
            continue

        # Two denominators, because the two rates answer different questions of different
        # episodes. Every unfaulted episode can say whether somebody was asked; only a
        # judged one can say whether the task was done, and counting an unjudged episode as
        # a failure would report a rig that cannot measure as a policy that cannot work.
        judged = [o for o in usable if o.succeeded is not None]

        points.append(
            InterventionPoint(
                cumulative_corrections=cumulative_corrections,
                intervention_rate=sum(1 for o in usable if o.interventions > 0) / len(usable),
                success_rate=(
                    sum(1 for o in judged if o.succeeded) / len(judged) if judged else None
                ),
                episodes=len(usable),
                judged=len(judged),
            )
        )

    return points


def is_significant(before: EvaluationResult, after: EvaluationResult) -> tuple[bool, str]:
    """Whether the difference between two intervention rates is worth claiming.

    A two-proportion z-test at roughly 95%. Deliberately conservative and deliberately
    returning its reasoning: this is the number the project is judged on, and "the rate
    went down" from twelve episodes is not a finding.

    Returns (significant, why). `why` is written to be quoted directly in a report, so
    that a negative result is as easy to publish as a positive one.
    """
    if not before.is_comparable or not after.is_comparable:
        return False, (
            "rates were measured under different confidence estimators, or under none, "
            "so they cannot be compared"
        )
    if before.confidence_sources != after.confidence_sources:
        return False, (
            f"estimators differ: {before.confidence_sources[0].value} before, "
            f"{after.confidence_sources[0].value} after"
        )
    if (
        before.episodes < _MIN_EPISODES_FOR_SIGNIFICANCE
        or after.episodes < _MIN_EPISODES_FOR_SIGNIFICANCE
    ):
        return False, (
            f"{before.episodes} and {after.episodes} episodes; at least "
            f"{_MIN_EPISODES_FOR_SIGNIFICANCE} each are needed before a difference "
            "means anything"
        )

    p1, n1 = before.intervention_rate, before.episodes
    p2, n2 = after.intervention_rate, after.episodes
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)

    if pooled in (0.0, 1.0):
        return False, (
            "every episode is on the same side in both sets, so there is no variance "
            "to test against"
        )

    standard_error = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / standard_error

    if abs(z) < 1.96:
        return False, (
            f"intervention rate moved {p1:.1%} to {p2:.1%}, but z={z:.2f} is within "
            "noise for these sample sizes"
        )

    direction = "fell" if p2 < p1 else "rose"
    return True, (
        f"intervention rate {direction} from {p1:.1%} to {p2:.1%} (z={z:.2f}, n={n1} and {n2})"
    )
