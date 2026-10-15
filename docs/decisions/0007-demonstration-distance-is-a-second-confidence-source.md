# ADR 0007 — Distance from successful demonstrations is a second confidence source

**Status:** accepted
**Date:** 2026-10-15
**Amends:** [ADR 0003](0003-confidence-has-no-upstream-source.md), which left sample spread
as the only estimator

## Context

ADR 0003 made confidence carry its source and shipped one estimator: sample the policy
several times and treat the spread as uncertainty. It named that estimator's hole in the
same breath. A policy that is confidently wrong samples tightly around the wrong motion and
reports high confidence, and that is the failure most worth catching. Spread also needs a
stochastic policy and several forward passes per chunk; a deterministic policy gets `NONE`.

ADR 0004 put the whole project on this estimate: if it is uninformative, the policy raising
its own hand collapses into what LeRobot already does. One estimator with a known blind
spot is a thin thing to rest that on.

The other sources ADR 0003 listed all need something missing. An ensemble costs a forward
pass per member against a latency budget that is already tight. A learned head needs the
labelled successes and failures the loop has not produced yet. Observation OOD says whether
the scene is familiar and nothing about the action.

## Decision

**A second estimator, `services/demonstration.py`, with its own source,
`ConfidenceSource.DEMONSTRATION`.** It indexes action chunks from successful episodes and,
at run time, measures how far the predicted chunk is from the nearest one that started from
a similar state. Far means the policy is about to do something no success did.

- **Successful means the existing labels.** `succeeded is True` from the evaluator and
  `interventions == 0`, the two fields `progress.EpisodeRecord` carries. Unknown is not
  success. Episodes an operator took over are left out with their corrections, because the
  store records where a handover started and not where the correction ended, so the motion
  the operator rejected cannot be separated from the one they supplied.
- **Conditioned on state.** A window is compared as two parts in joint space: how far
  apart the start states were, and how differently the chunks moved relative to their own
  start states. The same joint targets from a different place are a different motion.
- **Scaled like calibration.** The reference is the median, over every window, of the
  distance to the nearest window from another episode. Distances of exactly zero are left
  out, as `calibration.from_spreads` leaves out zero spreads: a home pose shared by every
  episode matches exactly, and held for most of each episode it made the median zero and
  the index answer "identical". Fewer than twenty non-zero distances is refused. Scored `1 / (1 + d / reference)`,
  the formula sample spread uses, so a chunk as far from the demonstrations as they are from
  each other scores 0.5.
- **Refuses rather than guesses.** No windows, fewer than two episodes, fewer than twenty
  windows, identical demonstrations, or a chunk that is too short, the wrong width, or not
  joint positions, or a chunk, state or demonstration holding a value that is not finite:
  `NONE`, with the reason. A non-finite demonstration is refused when the index is built,
  since one NaN made the scale NaN and every later chunk score 1.0.
- **numpy brute force.** No vector index and no new dependency.

## What it sees and what it cannot see

It sees a chunk unlike anything that worked from about here. That covers part of what
spread misses: a confidently wrong motion is caught when no success moved that way.

It cannot see that unlike is not wrong. A novel motion that would work is flagged, and a
recovery always is, since corrections are not indexed. A wrong motion that appears in
successful episodes passes, because success is judged per episode. With few demonstrations
most of the workspace is far from all of them, every score is low, and an operator who is
asked constantly learns to approve without looking.

## Combining with sample spread

Not done silently. The two are blind in different places, so when both are measured a
chunk should hand over if either scores below the threshold. Never an average, which lets
one confident signal hide the other's alarm. The rule is written here and in the module
docstring rather than as code, because nothing in the runtime builds an index yet (below),
and a combiner nothing calls is the kind of code this repository removes.

## Consequences

The shell's `ConfidenceSource` mirrors the new value; the contract test enforces it.

Evaluation must report which source produced a rate, as ADR 0003 already requires. A rate
under `demonstration` is not comparable with one under `chunk_variance`.

**Not wired into `tendon run` or the API.** The estimator needs, per stored episode, the
state and action columns and the evaluator's verdict. The store has the first two. The
verdict is in the progress log under the scheduler's episode id, the sidecar keys episodes
by the recorder's id, and the parquet numbers them from zero, so no column joins a verdict
to its frames. `recorder.finish` accepts `success` and is called without it. Until one of
those is fixed, the index can only be built by a caller who already knows which episodes
succeeded.
