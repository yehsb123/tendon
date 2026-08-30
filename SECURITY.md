# Security and safety

> **한글 요약.** 이 프로젝트는 소프트웨어 취약점보다 **물리적 안전**이 먼저입니다.
> v0.1은 시뮬레이션 전용이고, 실물 로봇에 쓰면 안 됩니다. 안전 문제를 발견하면
> 공개 이슈로 올리지 말고 비공개로 알려주세요.

Most projects put software vulnerabilities first. This one moves mass, so the order is
reversed.

## Current status: do not run this on physical hardware

**tendon is pre-alpha and simulation-only.** Nothing here has been verified against a
real body.

As of the current commit: `kernel/safety` **is** implemented and tested, including the
cases it cannot evaluate — a joint-space command carries no workspace information, and the
verdict reports that as `unchecked` rather than passing silently. But **nothing calls it
yet**, because `kernel/scheduler` is unwritten. A limit that exists and is never invoked
protects nobody, so treat this as unenforced until the scheduler lands.

Also still missing: any physical driver, and the interrupt path.

The `so101` driver named in `docs/roadmap.md` is v0.4 work. Until it exists, and until the
scheduler actually routes every action through `kernel/safety`, connecting this to a robot
means running a policy with no limit enforcement at all — the limits being written down
changes nothing on its own.

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

**A connection loss must not leave a body mid-motion.** Losing the shell holds position at
the control tier and stops new intent at the deliberation tier.

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
