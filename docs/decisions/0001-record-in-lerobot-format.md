# ADR 0001 — Record episodes in LeRobotDataset format

**Status:** accepted
**Date:** 2026-08-30

## Context

Design decision 1 makes every run an episode, so the on-disk format is settled before any
code is written and is expensive to change afterwards.

## Options

**A custom format.** Fits our needs exactly: intervention spans, confidence traces and
curation scores would be first-class rather than bolted on. But it is a new island. Nothing
else reads it, and no public dataset is usable without a converter.

**RLDS / TFDS**, as used by Open X-Embodiment. Well specified, and the format of the largest
public collection. TensorFlow-centric, and awkward to append to incrementally while a robot
is running.

**LeRobotDataset** (parquet + mp4). Already our robot control dependency. Appends cleanly
during live execution, reads natively in DuckDB, is directly trainable by LeRobot policies
and shareable on the Hub.

## Decision

Write LeRobotDataset. Read RLDS through conversion.

Fields specific to tendon — confidence traces, interrupt spans, operator corrections,
curation scores — live in a sidecar table keyed by episode and frame index, not as format
extensions. An episode therefore stays valid LeRobotDataset for any external consumer, while
tendon sees the richer view by joining.

## Consequences

Anything tendon records is trainable by the wider ecosystem on day one, and any Hub dataset
is replayable by tendon. We inherit format decisions from LeRobot, including breaking changes
before its 1.0. Accepted: interoperability is worth more than control.

The sidecar join is the cost. If it becomes the bottleneck for curation queries, revisit by
materializing a denormalized view, not by forking the format.
