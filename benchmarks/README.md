# benchmarks — measurements, and what they decided

> **한글 요약.** 여기 있는 숫자는 참고용이 아니라 **판정용**입니다. v0.1은 "레코더가
> 제어 루프를 느리게 만들면 폐기"라는 조건을 달고 있어서, 주장하지 않고 측정했습니다.
> 결론: **기록 자체는 예산의 0.3%로 사실상 공짜**, 그러나 **제어 루프 안에서 카메라를
> 동기 렌더하면 예산의 220%** 로 100Hz 루프가 성립하지 않습니다. 이건 레코더 문제가
> 아니라 루프 설계 문제입니다.

A measurement here exists because a decision hangs on it. Anything measured out of
curiosity belongs in a commit message, not in this directory.

Every number below was produced on CPU with no GPU and no hardware, which is the same
constraint `CONTRIBUTING.md` puts on the test suite. If a benchmark needs more than a
laptop, its result cannot be reproduced by whoever has to question it later.

---

## 1. Recorder overhead — the v0.1 kill condition

```bash
python benchmarks/recorder_overhead.py            # 300 steps at 100Hz
python benchmarks/recorder_overhead.py --steps 1000 --control-hz 50
```

`docs/roadmap.md` states the condition plainly:

> **Kills the milestone:** the recorder measurably slows the control loop. If recording is
> a cost, it will be switched off, and decision 1 fails.

**Setup.** One control step is `apply` + `observe` — what the scheduler does every tick —
optionally followed by `record`. MuJoCo driver, SO-ARM100 cube scene, 100 Hz control, so
the budget per step is 10 ms. The wrist camera renders at 240×320, deliberately small:
the question is whether rendering fits at all, and a small frame is the most favourable
case available.

**Result** (300 steps, all times in milliseconds, three runs):

| | mean | p50 | p99 | max | of budget |
| --- | --- | --- | --- | --- | --- |
| recorder off | 0.100 | 0.093 | 0.215 | 0.315 | 1.0% |
| recorder on, no camera | 0.126 | 0.108 | 0.448 | 1.282 | 1.3% |
| recorder on + wrist render | 21.945 | 21.147 | 36.201 | 46.940 | **219.4%** |

Overhead attributable to recording: **+0.027 ms**, about 0.3% of the control period.
Across three runs it measured +0.025, +0.031 and +0.027 ms — stable, and far below the
point where anyone would notice.

### What this decides

**Design decision 1 survives on the recording side.** Recording is not a cost, so the
argument that a recorder which can be switched off will be switched off does not apply:
there is nothing to gain by switching it off. `services/recorder.py` keeps the hot path
free by only appending to in-memory buffers — LeRobot's writer batches and encodes on
`save_episode`, and the DuckDB sidecar is written once per episode. Nothing touches the
disk at control rate.

**Synchronous rendering does not fit, and cannot be optimised into fitting.** At 21.9 ms
against a 10 ms budget it is not close, and p99 reaches 36 ms. Halving the frame size or
tightening the recorder does not recover a factor of two.

That is a finding about the loop, not about the recorder. On a real robot a camera arrives
asynchronously at ~30 fps over USB or ethernet and never blocks control; rendering inline
was the wrong model of a camera in the first place. Frames need their own clock, the way
the deliberation and control tiers already have separate clocks in
`docs/architecture.md`. The fix belongs next to `kernel/scheduler.py`, which is why
`drivers/mujoco.py` exposes `render()` as a separate call rather than folding pixels into
`observe()`.

**A concrete consequence for v0.2.** SmolVLA defaults to a 50-step action chunk. At 100 Hz
that is 0.5 s of intent, and 0.5 s is also about 15 camera frames at 30 fps. So the camera
clock and the deliberation clock are within a factor of two of each other, and neither is
anywhere near the control clock. Three tiers, not two.

---

## 2. Driver conformance — measured, not asserted

Not a timing benchmark; a set of facts about the body that the numbers above depend on.
Reproduced by `benchmarks/recorder_overhead.py` implicitly, and checked directly while
implementing `drivers/mujoco.py`.

**SO-ARM100 geometry**, measured from the loaded model rather than read off a datasheet:

| Quantity | Measured | Used for |
| --- | --- | --- |
| Actuators | 6 (5 arm joints + `Jaw`) | `Capability.dof` reports 5; the jaw is `Action.gripper` |
| Jaw gap, fully closed | 16 mm | lower bound on graspable object size |
| Jaw gap, fully open | 104 mm | upper bound |
| Grasp point, `Fixed_Jaw` frame | (-0.0087, -0.0823, 0) m | where the wrist camera is aimed |
| Max horizontal reach at table height | 0.377 m | where the cube can be spawned |

The 30 mm cube and its spawn at 0.25 m along −y both follow from these, rather than from
a guess that happened to work.

**Timing conformance.** 50 control steps at 100 Hz advance simulation time to exactly
0.500 s, so the driver's substep arithmetic — 5 physics steps of 2 ms per control step —
holds. A joint commanded to +0.5 rad reaches 0.499 rad. Gripper commanded fully open then
fully closed reads 1.000 then 0.000.

**Stability.** After 1.0 s of settling with no command, the cube holds z = 0.0150 m: it
rests on the floor rather than sinking through it or being launched by it. This is the
check that catches a bad `solimp`/`solref` before it becomes a mysterious training signal.

---

## 3. Round trip — recorder into human driver

The recorder and the replay driver verify each other, which is cheaper than trusting
either alone.

Wrote two episodes with `services/recorder.py`, then replayed them through
`drivers/human.py`:

| | Written | Replayed |
| --- | --- | --- |
| Episode 0 | 40 frames | 40 frames |
| Episode 1 | 15 frames | 15 frames |
| Gripper action | ramped 1.00 → 0.03 | 1.00 → 0.03 |

`Capability` was reconstructed from the dataset schema alone as `dof=5`, parallel gripper,
100 Hz, cameras `(wrist,)`, `readonly=True`. Rendered frames come back as `(240, 320, 3)`
`uint8` — the same shape and dtype the MuJoCo driver produces, so a consumer does not
branch on body type.

**What this decides.** Design decision 3 is testable rather than asserted: a simulated body
and a recorded body are interchangeable through the same HAL. The sidecar held 55 rows
across 2 episodes with 5 intervention frames, so confidence and intervention survive the
round trip too.

---

## 4. Environment findings worth not rediscovering

Not benchmarks, but the cost of finding them again is real.

- **MuJoCo cannot open non-ASCII absolute paths on Windows.** The file is readable; only
  MuJoCo's byte-oriented path API fails, reporting a path full of replacement characters.
  8.3 short paths do not help — `GetShortPathNameW` only shortens components over eight
  characters, so a short non-ASCII directory survives. `drivers/mujoco.py` loads with the
  working directory moved to the scene folder instead.
- **cp949 consoles cannot print an em dash.** A Windows console in a Korean locale raises
  `UnicodeEncodeError` mid-print. Benchmark output is plain ASCII for that reason — a
  script that crashes while printing its own conclusion is worse than one that never ran.
- **`pip install lerobot` cannot open a dataset.** `LeRobotDataset` needs lerobot's
  `dataset` extra, which pins `av>=15,<16`; a bare `pip install av` resolves to 18.x and
  fails with `module 'av' has no attribute 'option'`. There is no `torchcodec` wheel for
  Windows, so LeRobot falls back to pyav with a warning.

---

## Adding a benchmark

Two rules, both about keeping this directory small.

1. **Name the decision it settles.** If the result would not change what anyone does, it
   is a commit message, not a benchmark.
2. **Make it exit non-zero when the answer is bad.** `recorder_overhead.py` fails if
   recording alone exceeds a tenth of the control period. A benchmark that only prints is
   a benchmark nobody runs twice.
