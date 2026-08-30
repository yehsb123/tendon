# ADR 0005 — RTC is wrapped at the service layer, not inside the kernel

**Status:** accepted
**Date:** 2026-08-30
**Follows:** [ADR 0004](0004-lerobot-already-does-half-of-this.md), which said to evaluate
building on `rollout/` before writing more of the scheduler

## What was read

**`rollout/inference/rtc.py`** (602 lines). A real-time chunking engine: a background
thread produces action chunks via `policy.predict_action_chunk` while the main control
loop polls `get_action` for the next ready action, with observations flowing back through
`notify_observation`. It has `start`, `stop`, `pause`, `resume`, `reset`, tracks inference
latency, merges chunks across a prediction boundary, and re-anchors relative actions when
a new chunk arrives mid-execution.

This is a genuine solution to the two-clock problem, tested against a real control
deadline, and considerably more careful than anything `kernel/scheduler` would arrive at
in its first version. `pause`/`resume` even map onto handover.

**`rollout/ring_buffer.py`** (112 lines). Contrary to the earlier report, this is *not*
part of the two-clock machinery. It is a memory- and time-bounded telemetry buffer for the
Highlight Reel strategy — `append`, `drain`, `clear`, with byte accounting and a
single-threaded contract. Useful, unrelated.

## The problem

`RTCInferenceEngine` imports `torch`, `PreTrainedPolicy`, `lerobot.policies.rtc`, and four
processor steps from `lerobot.processor`. It is written against LeRobot's policy interface,
not against an abstract one.

`docs/architecture.md` states that the kernel may import pydantic and numpy, and must not
import torch, mujoco, any driver, or any service. `tests/unit/test_boundaries.py` enforces
it. Using RTC inside `kernel/scheduler` would mean:

- the kernel depends on torch, so a bare checkout can no longer run the unit suite
- the kernel depends on LeRobot's policy interface, so `Policy` stops being an abstraction
  and becomes an alias for `PreTrainedPolicy`
- a scripted controller or a replayed demonstration can no longer be a policy, which is
  what makes evaluation against a fixed baseline possible

That last one is the expensive part. The abstraction is load-bearing for evaluation, not
decoration.

## Decision

**The kernel scheduler is written here, thin, against `Driver` and `Policy` only.** It owns
the boundary between the two tiers, safety checking on every action, and interrupt
transitions. It knows nothing about how a chunk is produced.

**RTC is wrapped at the service layer** as a `Policy` implementation, when a LeRobot policy
is actually being run. That adapter may import torch freely — `services/` is permitted to.
The scheduler cannot tell the difference between it and a scripted controller.

**`ring_buffer.py` is not adopted.** The recorder writes LeRobotDataset directly (ADR 0001),
so a separate in-memory telemetry buffer solves a problem tendon does not have.

## Consequences

The first scheduler is synchronous. Asynchronous chunk production is exactly what RTC does
well, and duplicating it badly in the kernel would be the worst of both. A synchronous loop
is enough to run MuJoCo faster than real time, which is all v0.1 through v0.3 need, and the
`Policy` protocol does not change when an async adapter arrives behind it.

We give up RTC's chunk merging and re-anchoring in the kernel path. Both matter on physical
hardware where a new chunk arrives while the previous one is mid-execution. Both become
available through the adapter, at the point where a real robot makes them necessary.

The confidence estimator needs *n* samples from one observation, which RTC's engine is not
shaped to provide — it produces one chunk per prediction. The adapter has to expose a
sampling path alongside the streaming one, and that is noted for whoever writes it.
