"""Confidence estimation — the fifth thing tendon builds, and the load-bearing one.

No upstream policy reports how sure it is. LeRobot, OpenVLA and GR00T all return a bare
action tensor, and `confidence` appears nowhere in LeRobot's `rollout/` or `policies/`.
Every handover in that stack is started by a human who is already watching.

The one thing that makes tendon different is a policy raising its own hand, and that rests
entirely on this module. If what is here turns out to be uninformative, design decisions 1
and 2 collapse into things LeRobot already does. See
`docs/decisions/0004-lerobot-already-does-half-of-this.md`.

## The estimator

Stochastic policies — diffusion and flow-matching, which covers SmolVLA and pi-0 — give a
different action chunk each time they are sampled on the same observation. Where the policy
knows what to do, those samples agree. Where it does not, they scatter.

Sample n times, measure the spread, call the spread uncertainty.

## What it cannot see

**A confidently wrong policy.** Samples agree tightly on a motion toward entirely the wrong
place, and this reports high confidence. That is the failure mode most worth catching and
this estimator is blind to it. Catching it needs instruction-action consistency, which
needs a model — v0.3 work, listed in ADR 0003 as a known hole.

**Anything about a deterministic policy.** Sampling one twice gives the same answer, so the
spread is zero and the score is 1.0 regardless of the situation. `estimate` refuses rather
than returning that number: a deterministic policy has to use a different source.

## Why it is uncalibrated, and why that is said out loud

Spread has no absolute meaning. A 0.02 rad disagreement is nothing on a coarse gripper and
large on a precision wrist, so the score is computed against a reference scale rather than
a fixed threshold — the same argument as jerk in `curator.py`. Until v0.3 calibrates
against intervention outcomes, the reference is a configured guess, and the shell shows
which estimator produced the number rather than presenting it as a measurement.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from tendon.kernel.types import Action, Confidence, ConfidenceSource

__all__ = [
    "ChunkSpread",
    "estimate_from_samples",
    "spread_of",
    "temporal_agreement",
]

#: How much weight the first action carries relative to the last in a chunk. Disagreement
#: about what to do *now* matters more than disagreement about the end of the horizon,
#: which will be replaced by the next prediction before it ever executes.
_TAIL_WEIGHT = 0.2

#: Fewer than this and a spread is noise rather than a measurement.
_MIN_SAMPLES = 3


@dataclass(frozen=True)
class ChunkSpread:
    """Disagreement across sampled action chunks.

    Raw measurement. `estimate_from_samples` turns it into a `Confidence`.
    """

    #: Time-weighted mean standard deviation across samples, in action units.
    weighted: float
    #: Largest standard deviation at any single step and dimension.
    peak: float
    #: Step index where `peak` occurred. Named so an operator can be told *when*.
    peak_step: int
    #: Dimension index where `peak` occurred.
    peak_dim: int
    #: Standard deviation at the first step only — the action about to execute.
    imminent: float
    samples: int


def spread_of(chunks: Sequence[Sequence[Action]]) -> ChunkSpread:
    """Measure disagreement across chunks sampled from the same observation.

    Chunks may differ in length; comparison stops at the shortest, since a step only some
    samples predicted has nothing to disagree about.

    Raises `ValueError` below `_MIN_SAMPLES`, rather than reporting a spread of zero from
    one sample. Zero spread and no measurement look identical in a float and are opposite
    in meaning — the same distinction `ConfidenceSource.NONE` exists to preserve.
    """
    if len(chunks) < _MIN_SAMPLES:
        raise ValueError(
            f"need at least {_MIN_SAMPLES} samples to measure spread, got {len(chunks)}"
        )

    horizon = min(len(chunk) for chunk in chunks)
    if horizon == 0:
        raise ValueError("sampled chunks are empty")

    width = min(len(action.values) for chunk in chunks for action in chunk[:horizon])
    if width == 0:
        raise ValueError("sampled actions carry no values")

    weighted_total = 0.0
    weight_total = 0.0
    peak = 0.0
    peak_step = 0
    peak_dim = 0
    imminent = 0.0

    for step in range(horizon):
        # Linear decay from 1.0 at the imminent action to _TAIL_WEIGHT at the horizon.
        weight = 1.0 if horizon == 1 else 1.0 - (1.0 - _TAIL_WEIGHT) * (step / (horizon - 1))
        step_peak = 0.0

        for dim in range(width):
            values = [chunk[step].values[dim] for chunk in chunks]
            deviation = _stdev(values)

            weighted_total += deviation * weight
            weight_total += weight
            step_peak = max(step_peak, deviation)

            if deviation > peak:
                peak, peak_step, peak_dim = deviation, step, dim

        if step == 0:
            imminent = step_peak

    return ChunkSpread(
        weighted=weighted_total / weight_total if weight_total else 0.0,
        peak=peak,
        peak_step=peak_step,
        peak_dim=peak_dim,
        imminent=imminent,
        samples=len(chunks),
    )


def estimate_from_samples(
    chunks: Sequence[Sequence[Action]],
    *,
    reference_spread: float,
    deterministic: bool = False,
) -> Confidence:
    """Turn sampled chunks into a confidence, with reasons an operator can act on.

    `reference_spread` is the disagreement considered typical for this skill on this body
    — in the same units as the action values. Spread has no absolute meaning, so scoring
    against a fixed threshold would score the hardware rather than the situation.

    `deterministic` marks a policy that returns the same chunk every time. Such a policy
    produces zero spread regardless of the situation, so this refuses to dress that up as
    certainty and returns `ConfidenceSource.NONE` instead.
    """
    if deterministic:
        return Confidence(
            score=0.0,
            source=ConfidenceSource.NONE,
            reasons=(
                "policy is deterministic, so sample spread measures nothing; "
                "confidence-based handover is disabled for it",
            ),
        )

    spread = spread_of(chunks)

    if reference_spread <= 0.0:
        return Confidence(
            score=0.0,
            source=ConfidenceSource.NONE,
            reasons=("no reference spread configured, so the measurement has no scale",),
        )

    # Bounded rather than linear: one wildly disagreeing dimension should pull the score
    # down without any single value being able to drive it to exactly zero, which would
    # make every large disagreement look identical.
    score = 1.0 / (1.0 + spread.weighted / reference_spread)

    reasons: list[str] = []
    ratio = spread.weighted / reference_spread
    if ratio > 2.0:
        reasons.append(
            f"samples disagree {ratio:.1f}x more than usual for this skill "
            f"({spread.samples} samples)"
        )
    if spread.imminent > reference_spread * 2.0:
        # Worth saying separately: disagreement about the very next action is a different
        # situation from disagreement about the far end of the horizon, and an operator
        # deciding in two seconds needs to know which one they are looking at.
        reasons.append(
            f"disagreement is on the action about to execute, not the far horizon "
            f"({spread.imminent:.4f})"
        )
    if spread.peak > reference_spread * 4.0:
        reasons.append(
            f"largest disagreement at step {spread.peak_step}, dimension "
            f"{spread.peak_dim} ({spread.peak:.4f})"
        )

    return Confidence(
        score=max(0.0, min(1.0, score)),
        source=ConfidenceSource.CHUNK_VARIANCE,
        reasons=tuple(reasons),
    )


def temporal_agreement(
    previous: Sequence[Action],
    current: Sequence[Action],
    *,
    consumed: int,
) -> float | None:
    """How well a new chunk agrees with the unexecuted tail of the previous one.

    A second signal that costs nothing: consecutive predictions overlap, because a chunk
    covering 0.5s is replaced before it finishes. Where the policy is settled, the new
    chunk continues the old one; where it is not, the plan changes under itself.

    Unlike sampling, this needs no extra forward passes — the chunks already exist. It is
    also blind in a different way, since a policy can be consistently wrong across
    predictions just as easily as within one.

    Returns mean absolute disagreement over the overlap, or None when there is no overlap
    to compare. `consumed` is how many steps of `previous` were executed before `current`
    was issued.
    """
    if consumed < 0:
        raise ValueError(f"consumed must not be negative, got {consumed}")

    tail = previous[consumed:]
    horizon = min(len(tail), len(current))
    if horizon == 0:
        return None

    width = min(len(a.values) for a in list(tail[:horizon]) + list(current[:horizon]))
    if width == 0:
        return None

    total = 0.0
    count = 0
    for step in range(horizon):
        for dim in range(width):
            total += abs(tail[step].values[dim] - current[step].values[dim])
            count += 1

    return total / count if count else None


def _stdev(values: Sequence[float]) -> float:
    """Population standard deviation.

    Population rather than sample: these are all the samples drawn, not an estimate of a
    wider set, and with n small the Bessel correction inflates the number enough to matter
    against a reference scale.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(variance)
