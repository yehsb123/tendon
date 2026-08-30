"""Does v0.1 actually run? Driver, policy, scheduler, recorder, and back again.

`docs/roadmap.md` states the milestone:

> **Done when:** `tendon run` executes a policy in simulation and episodes appear in
> LeRobotDataset format without any collection flag being set.

This checks that, end to end, in one process, on a laptop:

    ScriptedPolicy -> Scheduler -> MujocoDriver -> Bus -> Recorder -> LeRobotDataset
                                                                          |
                                                            HumanDriver <-+

The round trip at the end matters as much as the forward path. An episode that writes but
cannot be read back is not data, and the two halves were built separately — the recorder
against LeRobot's writer, the replay driver against its reader. Making them verify each
other is cheaper than trusting either.

Exits non-zero if any stage fails, so this is a regression check on the whole milestone
rather than a demonstration of it.

Run:  python benchmarks/end_to_end.py
Needs the sim and robot extras. No GPU, no hardware, no model download.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

import tendon.drivers.human  # noqa: F401  registers the driver
import tendon.drivers.mujoco  # noqa: F401  registers the driver
from tendon.drivers.base import load
from tendon.kernel.bus import Bus
from tendon.kernel.protocols import Policy
from tendon.kernel.scheduler import Scheduler, StepRecord
from tendon.kernel.types import SafetyLimits
from tendon.services.policy_scripted import ScriptedPolicy
from tendon.services.recorder import Recorder

SUCCESS_HEIGHT_M = 0.1  # skills/grasp/cube-sim/skill.yaml
CAMERA = "wrist"
FRAME_SIZE = (240, 320)

# Generous, and deliberately so. This is not a safety test; it is here to confirm every
# action passes through `safety.check` on the way to the driver, which the scheduler
# guarantees by having exactly one `driver.apply` call site.
MAX_JOINT_VELOCITY = 8.0  # [rad/s]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="do not delete the episode store")
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="tendon-e2e-"))
    failures: list[str] = []

    try:
        driver = load(
            "mujoco",
            render_cameras=(CAMERA,),
            render_size=FRAME_SIZE,
            render_hz=30.0,
        )
        capability = driver.capability
        policy = ScriptedPolicy(control_hz=capability.control_hz)

        print(
            f"\nbody   {capability.body_id}  dof={capability.dof} "
            f"gripper={capability.gripper.value} {capability.control_hz:.0f}Hz"
        )
        print(
            f"policy {policy.name}  {policy.plan_steps} steps "
            f"({policy.plan_steps / capability.control_hz:.1f}s)"
        )

        if not isinstance(policy, Policy):
            failures.append("ScriptedPolicy does not satisfy the Policy protocol")

        bus: Bus[StepRecord] = Bus()
        recorder = Recorder(root=root, use_videos=False)
        recorder.start(policy.name, capability, cameras=(CAMERA,), frame_size=FRAME_SIZE)
        recorder.attach_to(bus, frames=driver.render)
        print(f"bus    subscribers={bus.subscribers}")

        result = Scheduler(
            driver=driver,
            limits=SafetyLimits(max_joint_velocity=MAX_JOINT_VELOCITY),
            bus=bus,
        ).run_episode(policy, max_steps=policy.plan_steps)

        print(f"\nepisode {result.episode_id[:8]}")
        print(f"  steps               {result.steps}")
        print(f"  ended               {result.state.value}")
        print(f"  interventions       {result.interventions}")
        print(f"  subscriber failures {list(result.subscriber_failures) or 'none'}")
        if result.unchecked:
            # Not a failure. `safety.check` reports what it could not evaluate rather than
            # passing it silently, and a position command cannot be checked for velocity
            # without a previous action — true of the first step of every episode.
            print(f"  unchecked           {len(result.unchecked)} limit(s), see safety.py")

        if result.subscriber_failures:
            failures.append(f"subscribers died mid-episode: {result.subscriber_failures}")
        if result.steps != policy.plan_steps:
            failures.append(f"ran {result.steps} steps, expected {policy.plan_steps}")

        height_m = float(driver.body_position("cube")[2])
        frames_rendered = driver.frames_rendered
        print(f"  cube height         {height_m:.4f} m")
        if height_m <= SUCCESS_HEIGHT_M:
            failures.append(f"cube ended at {height_m:.4f} m, below {SUCCESS_HEIGHT_M} m")

        meta = recorder.finish(success=height_m > SUCCESS_HEIGHT_M)
        driver.close()

        print(
            f"\nrecorded  steps={meta.steps} success={meta.success} camera frames={frames_rendered}"
        )
        if meta.steps != result.steps:
            failures.append(f"recorded {meta.steps} steps but the episode ran {result.steps}")

        # ---- the round trip: read back what was just written
        dataset_root = root / "tendon__local"
        replay = load("human", repo_id="tendon/local", root=dataset_root, episode=0)
        replayed = 1
        replay.reset()
        while replay.advance():
            replayed += 1
        replay_capability = replay.capability
        replay.close()

        print(f"replayed  {replayed} frames as {replay_capability.body_id}")
        if replayed != meta.steps:
            failures.append(f"replayed {replayed} frames, recorded {meta.steps}")
        if replay_capability.dof != capability.dof:
            failures.append(
                f"replay inferred dof={replay_capability.dof}, body has {capability.dof}"
            )
        if not replay_capability.readonly:
            failures.append("a replayed episode reported itself as commandable")

        print()
        if failures:
            for line in failures:
                print(f"  FAIL: {line}")
            return 1
        # ASCII only in printed output: a Windows console in a Korean locale runs cp949
        # and cannot encode an em dash, so a script that finishes its work and then
        # crashes while announcing it reads as a failure. Same reason as
        # recorder_overhead.py.
        print("  PASS: v0.1 runs end to end - policy to body to dataset and back.")
        return 0
    finally:
        if args.keep:
            print(f"  episode store kept at {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
