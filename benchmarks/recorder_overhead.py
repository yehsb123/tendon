"""Does recording measurably slow the control loop?

This is not a benchmark for curiosity. `docs/roadmap.md` states it as the condition that
kills the v0.1 milestone:

> **Kills the milestone:** the recorder measurably slows the control loop. If recording is
> a cost, it will be switched off, and decision 1 fails.

So it is measured rather than argued about, and the measurement is kept runnable so the
answer can be re-checked after any change to the driver or the recorder.

Run:  python benchmarks/recorder_overhead.py
Needs the sim and robot extras. No GPU, no hardware.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import tempfile
import time
from pathlib import Path

import tendon.drivers.mujoco  # noqa: F401  registers the driver
from tendon.drivers.base import load
from tendon.kernel.types import Action, ActionSpace
from tendon.services.recorder import Recorder

# Rendered small on purpose. The question is whether rendering fits in a control period at
# all; a larger frame only makes an already-failing answer worse, and a smaller one is the
# most favourable case we can give it.
FRAME_SIZE = (240, 320)  # (height, width) [px]


def measure(
    steps: int,
    *,
    record: bool,
    cameras: tuple[str, ...] = (),
    control_hz: float = 100.0,
    render_hz: float = 0.0,
) -> tuple[list[float], int]:
    """Time one control step, repeatedly.

    Returns the per-step durations in [ms] and how many distinct camera frames were
    produced, which is the number that says whether a recorded video is actually moving.

    A control step is `apply` plus `observe` — what the scheduler does every tick —
    optionally followed by the recording call. The dataset is written to a temporary
    directory that is removed afterwards, so repeated runs do not append to each other and
    measure a growing store instead of a fixed one.
    """
    root = Path(tempfile.mkdtemp(prefix="tendon-bench-"))
    try:
        driver = load(
            "mujoco",
            control_hz=control_hz,
            render_cameras=cameras,
            render_size=FRAME_SIZE,
            render_hz=render_hz,
        )
        recorder = None
        if record:
            recorder = Recorder(root=root, use_videos=False)
            recorder.start(
                "benchmark",
                driver.capability,
                cameras=cameras,
                frame_size=FRAME_SIZE,
            )

        observation = driver.reset()
        hold = list(observation.proprio.joint_positions)
        action = Action(space=ActionSpace.JOINT_POSITION, values=hold, gripper=0.5)

        # One untimed step first. The renderer allocates its GL context lazily, and that
        # one-off cost belongs to startup rather than to the steady state being measured.
        warmup = driver.apply(action)
        driver.observe()
        if recorder is not None:
            recorder.record(observation, warmup, frames=driver.render() if cameras else None)

        frames_at_start = driver.frames_rendered if cameras else 0
        durations: list[float] = []
        for _ in range(steps):
            started = time.perf_counter()
            # `apply` returns what the body executed, which is what gets recorded.
            applied = driver.apply(action)
            observation = driver.observe()
            if recorder is not None:
                recorder.record(
                    observation,
                    applied,
                    frames=driver.render() if cameras else None,
                    confidence=0.8,
                )
            durations.append((time.perf_counter() - started) * 1000.0)

        # Read before `finish`. The camera thread keeps running through episode encoding,
        # which takes seconds, and folding those frames in would report a frame rate the
        # control loop never saw. A delta for the same reason: it also runs while the
        # dataset is being created.
        rendered = (driver.frames_rendered - frames_at_start) if cameras else 0

        if recorder is not None:
            recorder.finish(success=True)
        driver.close()
        return durations, rendered
    finally:
        shutil.rmtree(root, ignore_errors=True)


def summarise(label: str, measured: tuple[list[float], int], budget_ms: float) -> float:
    durations, rendered = measured
    ordered = sorted(durations)
    mean = statistics.mean(ordered)
    frames = f"{rendered:5d} frames" if rendered else "          -"
    print(
        f"  {label:<30} mean {mean:7.3f}  p50 {ordered[len(ordered) // 2]:7.3f}  "
        f"p99 {ordered[int(len(ordered) * 0.99)]:7.3f}   "
        f"{mean / budget_ms * 100:6.1f}% of budget  {frames}"
    )
    return mean


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--control-hz", type=float, default=100.0)
    args = parser.parse_args()

    budget_ms = 1000.0 / args.control_hz
    print(
        f"\ncontrol step = apply + observe [+ record]   "
        f"{args.steps} steps at {args.control_hz:.0f}Hz, budget {budget_ms:.1f} ms\n"
    )
    print("  all times in milliseconds")
    baseline = summarise("recorder off", measure(args.steps, record=False), budget_ms)
    recording = summarise("recorder on, no camera", measure(args.steps, record=True), budget_ms)
    rendering = summarise(
        "  + wrist render, inline",
        measure(args.steps, record=True, cameras=("wrist",)),
        budget_ms,
    )
    threaded = summarise(
        "  + wrist render, 30Hz thread",
        measure(args.steps, record=True, cameras=("wrist",), render_hz=30.0),
        budget_ms,
    )

    print()
    print(f"  recording overhead                  {recording - baseline:+7.3f} ms")
    print(f"  render overhead, inline             {rendering - baseline:+7.3f} ms")
    print(f"  render overhead, on its own clock   {threaded - baseline:+7.3f} ms")
    print()

    # The threshold is deliberately generous. "Measurably slows" is the wording in the
    # roadmap, and a tenth of the control period is well past measurable.
    limit = budget_ms * 0.1
    if recording - baseline > limit:
        print(f"  FAIL: recording alone exceeds {limit:.2f} ms. Design decision 1 is at risk.")
        return 1
    print(f"  PASS: recording alone stays under {limit:.2f} ms.")
    if threaded - baseline > limit:
        print(f"  FAIL: threaded rendering still exceeds {limit:.2f} ms.")
        return 1
    print(f"  PASS: rendering on its own clock stays under {limit:.2f} ms.")
    if rendering > budget_ms:
        # Plain ASCII, deliberately. Windows consoles in a Korean locale run cp949, which
        # cannot encode an em dash, and a benchmark that crashes while printing its own
        # conclusion is worse than one that never ran.
        print(
            f"  NOTE: rendering inline does not fit the {budget_ms:.1f} ms budget; "
            f"render_hz moves it off the control clock. Frame counts above show the cost: "
            f"a camera on wall-clock time cannot keep up with simulation running flat out. "
            f"See benchmarks/README.md."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
