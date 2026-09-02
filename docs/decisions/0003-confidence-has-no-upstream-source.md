# ADR 0003 — Confidence has no upstream source, so tendon builds a fifth thing

**Status:** accepted
**Date:** 2026-08-30
**Found by:** Track A, reading LeRobot 0.6.2 at `4aaff99`

## Context

`docs/stack.md` says tendon builds four things: the Embodiment HAL, the interrupt
protocol, curation metrics, and the shell. That list is wrong, and it took reading the
upstream source to find out.

`InterruptReason.LOW_CONFIDENCE` is the trigger that makes design decision 2 fire during
normal operation. The other three reasons are edge paths: `SAFETY_TRIP` means a limit was
breached, `OPERATOR_REQUEST` means a human chose to step in, `DRIVER_FAULT` means something
broke. Only low confidence produces the ordinary case the whole project is about — a policy
noticing it is out of its depth and asking for help before acting.

And nothing upstream provides it. `PreTrainedPolicy` in LeRobot exposes `select_action` and
`predict_action_chunk`; both return a bare action tensor. No policy in the library reports
how sure it is. The same is true of OpenVLA and of the published GR00T interface.

So confidence estimation is a fifth thing tendon has to build. Discovering that at v0.3,
while trying to produce the graph the project is judged on, would be much worse than
recording it now.

## The trap to avoid

The tempting move is to default confidence to 1.0 when a policy does not report it. Every
interface keeps working, nothing raises, and the system appears to have a capability it
does not have. A robot that never asks for help looks identical to a robot that is always
right, right up until it is not.

Defaulting to 0.0 is equally wrong in the other direction — every step interrupts, and an
operator learns within an hour to approve without looking, which is worse than no
supervision because it manufactures a record of human oversight that did not happen.

Neither is acceptable. The absence of an estimate has to be representable.

## Options considered

**Action chunk variance.** Sample the policy several times on the same observation, or
measure internal disagreement across the chunk, and treat spread as uncertainty. Cheap, no
extra training, works with any stochastic policy — and diffusion and flow-matching policies
are stochastic by construction, which covers SmolVLA and π0. Weak where a policy is
confidently wrong, which is precisely the failure that most needs catching.

**Ensemble disagreement.** Run several policies or seeds and compare. Better signal, and
the cost is linear in ensemble size against a latency budget that already forced the
two-clock split. Not viable on one consumer GPU while also closing the nightly loop.

**An auxiliary confidence head.** Train the policy to predict its own success. The most
principled option and the one that needs what we do not have: a labelled dataset of
successes and failures, which is what the loop is supposed to produce. Available from v0.3
onward, not before.

**Out-of-distribution detection on observations.** Ask whether the current observation
resembles the training set rather than whether the action is right. Catches the new-object
case, which is the common one on a factory floor, and says nothing about a familiar scene
handled badly.

**No estimator at all.** Run with `SAFETY_TRIP` and `OPERATOR_REQUEST` only. Honest,
immediately available, and reduces the shell to a manual takeover tool — but it does
exercise the whole interrupt path end to end, which is what v0.2 needs to demonstrate.

## Decision

**Confidence carries its source.** `Confidence` gains a `source` field naming where the
number came from: `chunk_variance`, `ensemble`, `learned_head`, `ood`, or `none`.

When `source` is `NONE`, the score is not a measurement and must not be treated as one.
`should_raise` refuses to fire on it, so a policy with no estimator falls back to
safety-trip and operator-request interrupts rather than silently never asking for help.

**v0.2 ships chunk variance** as the default estimator, uncalibrated, and the shell says so
rather than presenting a bare number as though it meant something.

**v0.3 calibrates against intervention outcomes.** By then the loop has produced exactly
the labelled data an estimator needs: episodes where a human took over, and what happened
after. Calibration is a downstream use of the same data that produces the project graph.

`docs/stack.md` is corrected from four things to five.

## Consequences

The shell must distinguish "unsure" from "no estimate". Those look identical in a number
and are opposite in meaning, and an operator who cannot tell them apart will misread the
one case where it matters.

The `Policy` protocol demands an `Intent`, and therefore a `Confidence`, from every
implementation. Adapters wrapping upstream policies must state `NONE` rather than invent a
value. That is a real burden on adapter authors and it is deliberate: making the absence
explicit is the entire point.

Evaluation must report the estimator alongside the intervention rate. A rate measured under
`chunk_variance` is not comparable to one measured under a learned head, and publishing the
two on the same axis would be a false result — the kind that looks like progress.

The threshold in `skill.yaml` stays a starting point rather than a recommendation, and
becomes meaningful only once calibration exists.

## Postscript — "calibration" was two things

Acting on this held up more than it had to. "v0.3 calibrates against intervention
outcomes" was read as one blocked task, and it is two, only one of which needs the
outcomes:

- **A scale.** How much disagreement is *typical* for this policy on this body. A property
  of the policy and the body, measured by running them and looking at the distribution. No
  labels, no operator, no episodes. `services/calibration.py` and `tendon calibrate`.
- **A threshold.** How much disagreement means *ask for help*. A property of what goes
  wrong when you do not, which is only visible in episodes where somebody took over and
  what happened after. Still v0.3, still this decision as written.

The cost of conflating them was concrete: `estimate_from_samples` takes a
`reference_spread` that every caller had to supply and none could measure, so `api/app.py`
passed a constant fitted to its synthetic policy and the CLI passed zero. A loaded
checkpoint ran and reported `NONE`. Design decision 2 says *the policy raises its own
hand*, and for a real policy nothing could, for want of a number that was measurable the
whole time.

The scale does not make the threshold meaningful and must not be presented as though it
does. What it changes is that a score exists at all, and that two runs of the same policy
on the same body are now comparable to each other — which is the smaller claim, and true.
