# Security and safety

Most projects put software vulnerabilities first. This one moves mass, so the order is
reversed.

## Current status: do not run this on physical hardware

**tendon is pre-alpha and simulation-only.** Nothing here has been verified against a
real body.

As of the current commit, `kernel/safety` is implemented, tested, **and invoked**: the
scheduler routes every action through it before the action reaches a driver, including
corrections supplied by an operator. The interrupt path is implemented and exercised end
to end by `examples/04_improve`.

**A physical driver now exists.** `drivers/so101.py` drives an SO-101 over a serial port,
which changes what this notice is about. Until this commit the honest summary was "nothing
here can move a real robot". That is no longer true.

What has not changed is the evidence: **none of this has been verified against a real
body.** Every safety limit in this repository has only ever held in simulation, and a limit
that has only held in simulation has not been shown to hold. MuJoCo does not have backlash,
a servo that browns out under load, or a person standing where the arm is about to be.

So the instruction is the same and the reason is stronger: do not connect this to hardware
you are not prepared to have moved unexpectedly, and do not stand where it can reach.

Also still missing: authentication between the shell and the runtime, and a local policy
able to override a skill-declared safety limit.

**What has changed since this notice was first written.** Two paragraphs used to stand here
saying the `so101` driver was v0.4 work that did not exist yet, and that until the scheduler
routed every action through `kernel/safety` a robot would be running with no limit
enforcement at all. Both were true when written and neither is true now, and a safety
document that contradicts itself two paragraphs apart is worse than one that says less:

- `drivers/so101.py` exists, which is what the top of this section says.
- The scheduler has exactly one `driver.apply` call site and every action reaches it
  through `_check`. That is checkable by reading `kernel/scheduler.py`, and
  `tests/unit/test_scheduler.py` holds it.

So limit enforcement is real. **What is still missing is evidence that the limits are
right**, which is the paragraph above and the reason this notice stands.

This notice will be revised, not removed, when that changes.

## Reporting a safety issue

A safety issue is anything where the system could command a motion it should not have.
These take priority over every other class of bug.

Examples, in the order they should worry you:

1. An action reaching a driver without passing `kernel/safety`
2. A safety limit that can be exceeded through an operator correction
3. An interrupt that loses the context needed to resume, and degrades to a stop while
   still being reported as an interrupt
4. A driver silently substituting an action it could not execute
5. The shell showing stale state during an intervention — an operator deciding on old
   information is deciding wrongly

**Do not open a public issue.** Report through GitHub Security Advisories on this
repository, or contact the maintainer directly. Include what moved, what should have
stopped it, and whether hardware was attached.

## Reporting a software vulnerability

Same channel, same request: no public issue first. Relevant surfaces are the API, which
accepts operator corrections and therefore accepts commands that reach a body; the skill
registry, which resolves and installs code and weights from a remote hub; and episode
storage, which contains camera frames from wherever the robot is running.

## Design positions that carry security weight

These are properties the design is supposed to have. If one does not hold, that is a
report worth making even without a demonstrated exploit.

**Safety is independent of the policy.** `kernel/safety` checks every action against hard
limits, on the same path, regardless of origin. Safety that lives inside the thing being
supervised is not safety. See `docs/architecture.md`.

**An operator can correct but not exceed.** Corrections from the shell are subject to the
same limits as policy output. The interface must not be a way around a bound.

**An interrupt is not a stop.** If the saved context is insufficient to resume, the event
must be reported as a fault. Reporting a degraded interrupt as a normal one makes the
intervention rate look better than it is — and that number is the single metric this
project is judged on, so distorting it is both a safety issue and a research integrity
issue.

**A connection loss must not leave a body mid-motion.** Implemented, and worth describing
precisely — an earlier version of this section claimed it while the code did nothing of the
kind, and the sentence that replaced *that* said it was only partly done. Both have now been
outlived, which is the direction a safety notice should move in only after the code moves
first.

What happens: an episode that loses its **last** operator stops proposing new motion. The
committed chunk finishes and the scheduler declines to ask for another, which is the
deliberation tier stopping while the control tier holds. Cutting a chunk short would be the
opposite of safe — a stop that is itself a motion nobody chose, on a body mid-reach.

If the policy had already handed over, the pending decision is given up on rather than
waited out: `ShellHandler` checks between short slices of its wait instead of sleeping the
full `timeout_s`. A handover with nobody connected is the case most worth ending — the body
is held, the question has been asked, and the only thing that could answer it has gone.
Either way the episode is **aborted, never approved**: nobody answered, so nobody approved.

The session records which of the two ended it, so a short run is not mistaken for a
completed one.

The condition is *somebody was watching and now nobody is*, not *nobody is watching*. An
episode nobody has connected to yet is ordinary — the shell posts and then opens the socket,
and `tendon run` never connects at all — and stopping those would stop the runs this
protects.

**Skills are remote code.** `tendon install` fetches weights and configuration from the
Hugging Face Hub. A skill declares its own safety limits, which means an installed skill
proposes the bounds it runs under. Treat a skill from an untrusted namespace the way you
would treat any code you did not write, and review `skill.yaml` before running it. Local
policy overriding a skill-declared limit is unimplemented and is tracked as required work
before v0.4.

## Scope

Supported: `main`. There are no released versions to backport to yet.

Out of scope for now: the shell assumes a trusted network, and there is no authentication
between the shell and the runtime. Both are v0.4 work and are listed here so that nobody
mistakes their absence for an oversight.
