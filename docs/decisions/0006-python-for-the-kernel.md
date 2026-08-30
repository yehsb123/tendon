# ADR 0006 — Python for the kernel, and where that stops being acceptable

**Status:** accepted
**Date:** 2026-08-30
**Prompted by:** the obvious question — a robot control system in Python?

> **한 줄 요약 (KO).** 타당한 지적입니다. 파이썬은 GIL과 GC 때문에 하드 실시간에
> 부적합합니다. 하지만 **커널은 제어 루프가 아니라 오케스트레이션 층**입니다. 실제
> 고주파 제어는 드라이버 뒤(MuJoCo의 C, 로봇 펌웨어)에서 일어나고, 커널은 ~10Hz로
> "무엇을 할지"만 정합니다. 그리고 조합할 생태계(LeRobot·MuJoCo·PyTorch·Rerun)가
> 전부 파이썬입니다 — C++로 쓰면 이 프로젝트의 전략 자체가 불가능해집니다. 다만
> 경계는 명확히 합니다: 1kHz 하드 실시간이 필요해지면 그건 드라이버 층이고, HAL이
> 이미 그걸 허용합니다.

## The objection

Python has a global interpreter lock and a garbage collector. Both introduce
non-deterministic pauses. A control loop that must hit a deadline every 1ms cannot be
written in it, and anyone who has done real-time robotics knows this. The question is
fair and the answer is not "Python is fast enough now".

## What the kernel actually does

It is worth being precise about the workload, because the objection assumes a workload the
kernel does not have.

```
policy.predict()        ~1-10Hz     one call, returns a chunk of ~50 actions
  safety.check()        per action  a few comparisons, no allocation in the hot path
  driver.apply()        per action  hands a setpoint across the boundary
```

The kernel never closes a servo loop. It hands a setpoint to a driver and the driver — or
the hardware behind it — does the actual control. In MuJoCo that is C. On an SO-101 it is
firmware on the motor controller. On an industrial arm it is a real-time controller that
was doing this correctly before any of this existed.

This is what the two-clock split in `docs/architecture.md` is *for*. It was written to
absorb inference latency, and it has this as a second consequence: the tier that must meet
a deadline is not the tier written here.

## Why not C++ anyway

**The composition strategy would become impossible.** `docs/stack.md` says tendon depends
on what exists and writes only what does not. What exists is LeRobot, MuJoCo's Python
bindings, PyTorch, PEFT, Rerun, the Hugging Face Hub — all Python. A C++ kernel would have
to reimplement or bind every one of them, and then the four (now five) things tendon builds
would be dwarfed by the binding layer. The project would become a robotics framework, which
is a thing several teams with more people are already doing better.

**The contributor cost is the whole reason v0.1 is reachable.** `pytest tests/unit` runs on
a bare checkout with no compiler, no CMake, no toolchain. That property is load-bearing for
a project one person can start.

**The kernel is small and getting smaller.** `safety`, `interrupt`, `scheduler`, `bus`,
`types`, `protocols` — the whole thing is under 1500 lines including docstrings. Rewriting
it later is a week, not a rewrite of the system. Choosing C++ now would be paying that cost
before knowing whether the project's central claim survives v0.3.

## Where this stops being acceptable

Stated concretely so it is checkable rather than a matter of opinion.

| Situation | Python is fine | Python is not |
| --- | --- | --- |
| Setpoints at 100Hz to a driver that interpolates | yes | |
| Deliberation at 1-10Hz | yes | |
| Safety limits checked per action | yes | |
| Closing a torque loop at 1kHz | | no — GC pause exceeds the period |
| Hard real-time with a jitter guarantee | | no — needs an RT kernel and no GC |
| Force control during contact | | no — the deadline is the physics |

The last three belong in a driver, behind the HAL. `kernel/protocols.py` defines `Driver`
as a contract, not as a Python class hierarchy, so a driver that is a thin wrapper over a
C++ or Rust controller satisfies it exactly as well as `MujocoDriver` does. That is not a
hypothetical escape hatch — it is the same mechanism that lets a simulator, a physical arm
and recorded human video all be bodies.

## Decision

**Python for the kernel, services, and the CLI. The shell is TypeScript. Real-time control
lives behind a driver, in whatever language that driver needs.**

Revisit when either of these becomes true, and not before:

1. A body needs setpoints faster than a Python loop can deliver them *and* the driver
   cannot interpolate. Then the loop moves into that driver.
2. The scheduler is measured as the bottleneck in a real deployment. `kernel/bus.py`
   already measures subscriber cost per publish for exactly this reason — the argument
   should be settled with a number rather than a preference.

## Consequences

We accept jitter in the deliberation tier. A policy asked at 10Hz that occasionally arrives
at 9.2Hz changes nothing: the control tier is interpolating over a chunk and does not
starve.

We accept that tendon cannot ship a hard-real-time controller, and should not claim to. The
`SECURITY.md` position that this must not be connected to physical hardware yet is partly
about safety enforcement being unwired, and partly about this.

The measurement in `examples/01_record --overhead` is the honest version of this argument.
If recording in Python costs more than a few percent of loop time, that is evidence against
this ADR, and it is being collected either way.
