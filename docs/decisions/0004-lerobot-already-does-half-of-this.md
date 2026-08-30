# ADR 0004 — LeRobot already does half of this, so the claim narrows

**Status:** accepted
**Date:** 2026-08-30
**Found by:** Track A, reading `src/lerobot/rollout/` at LeRobot 0.6.2
**Verified:** independently, against the same source

## What was claimed

`docs/stack.md` said, of the interrupt protocol:

> The field has E-stop (cuts power) and teleop (full manual). Nothing in between
> preserves context.

That was written from the outside. It is wrong.

## What is actually there

`src/lerobot/rollout/` contains a supervision layer nobody outside the repository talks
about much:

| Module | What it does |
| --- | --- |
| `strategies/dagger.py` | Human takes over mid-policy. Corrections tagged `intervention=True` |
| `strategies/sentry.py` | Continuous autonomous recording with auto-upload |
| `inference/rtc.py`, `ring_buffer.py` | Real-time chunking against a control deadline |
| `strategies/highlight.py`, `episodic.py` | Episode segmentation and marking |
| `robot_wrapper.py`, `interactive.py` | Scheduler-adjacent machinery |

`DAggerPhase` is a three-state machine — `AUTONOMOUS`, `PAUSED`, `CORRECTING` — against
tendon's `RUNNING`, `PENDING`, `RESUMING`. It preserves context. It records corrections
as training data. On the transition into correction it *slides* the follower to the
teleoperator's pose so the operator does not inherit a jerk, which is a detail tendon had
not thought about and is better than what is written here.

So each of these overlaps a tendon design decision:

- `sentry.py` overlaps decision 1, running is collecting
- `dagger.py` overlaps decision 2, intervention is an interrupt
- `rtc.py` overlaps the two-clock split in `docs/architecture.md`

This is not a small overlap discovered at the edges. It is the middle of the project.

## What does not exist

Grepped the whole of `rollout/` and `policies/`. **`confidence` appears nowhere** — the
only hits in the repository are in `rewards/sarm/`, a reward model's stage confidence,
unrelated to policy execution.

Every transition in `DAggerPhase` is driven by an operator action: `pause_resume` and
`correction`, bound to a keyboard key or a foot pedal. **Every handover in LeRobot is
initiated by a human who is already watching.** Nothing in the stack lets the system
raise its own hand.

That single fact is what remains.

## Decision

**The claim narrows to three things, and the wording in `docs/stack.md` is corrected.**

1. **The handover is triggered by the policy's own uncertainty**, not by a human who
   happens to be watching. This is the difference between supervision that scales and
   supervision that requires one operator per robot, and it is the only one of the three
   that changes the economics.

2. **It happens before the body moves**, on a reviewable intent. DAgger's operator
   intervenes after seeing something go wrong; tendon's sees the proposed action chunk
   and decides beforehand. That is only possible because action chunking already exists
   for latency reasons — the plan is there to render.

3. **Curation metrics, skill packaging, and a body abstraction that includes human
   video.** None of these appear in `rollout/`, and the first is the one with no agreed
   answer anywhere.

Points 1 and 2 both rest on a confidence estimate that no upstream policy provides. So
[ADR 0003](0003-confidence-has-no-upstream-source.md) is not a scheduling detail — it is
the load-bearing element of the entire project. If confidence estimation does not work,
tendon is a worse-engineered DAgger with a nicer interface.

**Where possible, build on `rollout/` rather than beside it.** The porting rule applies as
it always did: prefer a dependency. `ring_buffer.py` and the RTC inference path solve the
two-clock problem against a real control deadline, which is more than the current
`kernel/scheduler` stub does. Track A should evaluate wrapping them before writing more of
that module.

## Consequences

`docs/stack.md` and both READMEs are corrected. The "nothing in between" line is removed,
and the interrupt row now says what is actually missing: a policy-initiated handover.

The v0.3 test gets sharper rather than weaker. It was "does the intervention rate fall
after corrections", which DAgger could also demonstrate. It becomes: **does a
policy-initiated handover catch failures that a watching human would have caught later,
and does correcting them reduce the rate?** The baseline is now DAgger rather than
nothing, and that is a harder and more honest comparison.

The risk this ADR names explicitly: if chunk-variance confidence turns out to be
uninformative, decisions 1 and 2 collapse into things LeRobot already does, and what
remains is curation and packaging. That would still be worth building, and it would not
be the project described in the README. Better to have that written down now than to
discover it at v0.3.

## What this does not change

Reading the source before claiming novelty is the rule that produced this document, and it
produced ADR 0002 the same way. Both times the claim got smaller and the project got more
defensible. The standing rule in `docs/stack.md` — search for the project that already does
it, and record what you found even when you build anyway — is doing its job.
