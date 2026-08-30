"""Is "a body is a driver" true, or just written down?

Design decision 3 says a policy addresses intent and a driver translates it, so the same
code drives any body. One robot cannot demonstrate that — it only shows the driver works
on that robot. Two robots that disagree can.

The SO-ARM100 and the UFACTORY xArm7 disagree on every axis a HAL has to absorb:

    arm joints            5                    7
    gripper transmission  joint                tendon
    gripper units         radians              0-255
    open end of range     upper (1.75 rad)     LOWER (0)
    pad separation        16-104 mm            7-93 mm

The fourth row is the dangerous one. Before this was handled, `Action.gripper = 1.0`
opened one gripper and closed the other, which no type checker and no unit test would
catch — both bodies would run, and one would crush what it was asked to pick up.

So this loads both through the same `MujocoDriver`, commands both through the same
`Action`, records both through the same `Recorder`, and checks that the differences show
up where they should (in `Capability` and in the dataset schema) and nowhere else (in the
meaning of a command).

Exits non-zero if any of that stops being true.

Run:  python benchmarks/two_bodies.py
Needs the sim and robot extras. No GPU, no hardware.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import tendon.drivers.mujoco  # noqa: F401  registers the driver
from tendon.drivers.base import load
from tendon.kernel.types import Action, ActionSpace
from tendon.services.recorder import Recorder, features_for

SCENES = Path(__file__).resolve().parents[1] / "src/tendon/assets/scenes"


@dataclass(frozen=True)
class Body:
    """A body under test, plus the two things a caller has to know about it.

    `gripper_opens_high` and the pad names are not discoverable from the model in any way
    the driver could trust, so they are stated. That is the honest shape of the
    abstraction: the driver absorbs the difference, but somebody has to declare it once.
    """

    name: str
    scene: str
    gripper_opens_high: bool
    #: Contact pad *geoms*, not finger bodies. An SO-ARM100 jaw rotates, so its two jaw
    #: bodies stay a fixed 32 mm apart at every opening while the pads swing from 16 mm to
    #: 104 mm — measuring bodies there reports a gripper that never moves. An xArm7's
    #: fingers translate, so bodies would have worked. Exactly the kind of difference that
    #: makes one robot a bad test of a HAL.
    pads: tuple[str, str]
    expected_dof: int


BODIES = (
    Body("SO-ARM100", "so_arm100_cube.xml", True, ("fixed_jaw_pad_3", "moving_jaw_pad_3"), 5),
    Body("xArm7", "xarm7_cube.xml", False, ("left_finger_pad_1", "right_finger_pad_1"), 7),
)

SETTLE_STEPS = 300


def measure(body: Body, root: Path) -> dict:
    """Drive one body through the same commands and report what came back."""
    driver = load(
        "mujoco",
        scene_path=SCENES / body.scene,
        gripper_opens_high=body.gripper_opens_high,
        render_cameras=("wrist",),
        render_size=(240, 320),
    )
    try:
        capability = driver.capability
        observation = driver.reset()
        hold = list(observation.proprio.joint_positions)

        gaps: dict[float, float] = {}
        reported: dict[float, float] = {}
        for command in (1.0, 0.0):
            for _ in range(SETTLE_STEPS):
                driver.apply(Action(space=ActionSpace.JOINT_POSITION, values=hold, gripper=command))
            observation = driver.observe()
            left, right = (driver.geom_position(n) for n in body.pads)
            gaps[command] = float(np.linalg.norm(left - right))
            reported[command] = float(observation.proprio.gripper_open)

        frames = driver.render()
        schema = features_for(
            capability, cameras=("wrist",), frame_size=(240, 320), use_videos=False
        )

        recorder = Recorder(root=root / body.name, use_videos=False)
        recorder.start(f"probe/{body.name}", capability, cameras=("wrist",), frame_size=(240, 320))
        for _ in range(10):
            applied = driver.apply(
                Action(space=ActionSpace.JOINT_POSITION, values=hold, gripper=0.5)
            )
            recorder.record(driver.observe(), applied, frames=driver.render())
        meta = recorder.finish(success=None)

        return {
            "capability": capability,
            "gaps": gaps,
            "reported": reported,
            "frames": {k: v.shape for k, v in frames.items()},
            "schema": schema,
            "recorded": meta.steps,
            "body_id": meta.body_id,
        }
    finally:
        driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="tendon-bodies-"))
    failures: list[str] = []
    try:
        results = {b.name: measure(b, root) for b in BODIES}

        print("\nsame driver, same Action, same Recorder:\n")
        print(f"  {'':<22}{'SO-ARM100':>18}{'xArm7':>18}")
        rows = [
            ("Capability.dof", lambda r: r["capability"].dof),
            ("gripper kind", lambda r: r["capability"].gripper.value),
            ("gripper=1.0 -> gap", lambda r: f"{r['gaps'][1.0] * 1000:.0f} mm"),
            ("gripper=0.0 -> gap", lambda r: f"{r['gaps'][0.0] * 1000:.0f} mm"),
            ("gripper=1.0 reported", lambda r: f"{r['reported'][1.0]:.3f}"),
            ("gripper=0.0 reported", lambda r: f"{r['reported'][0.0]:.3f}"),
            ("state feature", lambda r: str(r["schema"]["observation.state"]["shape"])),
            ("action feature", lambda r: str(r["schema"]["action"]["shape"])),
            ("wrist frame", lambda r: str(r["frames"]["wrist"])),
            ("recorded steps", lambda r: r["recorded"]),
        ]
        for label, get in rows:
            a, b = get(results["SO-ARM100"]), get(results["xArm7"])
            print(f"  {label:<22}{str(a):>18}{str(b):>18}")

        print()
        for body in BODIES:
            r = results[body.name]

            # The invariant the whole exercise is about.
            if r["gaps"][1.0] <= r["gaps"][0.0]:
                failures.append(
                    f"{body.name}: gripper=1.0 did not open it "
                    f"({r['gaps'][1.0] * 1000:.0f} mm vs {r['gaps'][0.0] * 1000:.0f} mm closed)"
                )
            if r["reported"][1.0] < 0.9 or r["reported"][0.0] > 0.1:
                failures.append(
                    f"{body.name}: reported opening did not follow the command "
                    f"(1.0 -> {r['reported'][1.0]:.3f}, 0.0 -> {r['reported'][0.0]:.3f})"
                )
            if r["capability"].dof != body.expected_dof:
                failures.append(
                    f"{body.name}: dof {r['capability'].dof}, expected {body.expected_dof}"
                )
            if r["schema"]["observation.state"]["shape"] != (body.expected_dof,):
                failures.append(f"{body.name}: state feature does not follow dof")
            if r["recorded"] != 10:
                failures.append(f"{body.name}: recorded {r['recorded']} steps, expected 10")

        # And the difference has to actually be a difference, or this proves nothing.
        if results["SO-ARM100"]["capability"].dof == results["xArm7"]["capability"].dof:
            failures.append("both bodies report the same dof; they are not distinct enough to test")

        if failures:
            for line in failures:
                print(f"  FAIL: {line}")
            return 1
        print("  PASS: one driver, two bodies that disagree, and gripper=1.0 means open on both.")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
