"""Run a scripted grasp and save what the cameras saw.

Two jobs, and the second one is why this is a script rather than a screenshot.

**It shows what the benchmarks are actually running.** `recorder_overhead.py` reports
milliseconds against a scene nobody has seen. This renders that scene at five points in a
pick-up so the numbers have a picture attached.

**It proves the scene works.** The sequence ends by checking the cube is above 0.1 m,
which is the success condition `skills/grasp/cube-sim/skill.yaml` declares. If the jaw
geometry, the friction, the cube size or the reach were wrong, this fails — and it fails
loudly, with an exit code, rather than producing a plausible picture of a robot missing.

No policy is involved. The joint targets come from inverse kinematics against the grasp
point measured in `benchmarks/README.md`, so this says the *body* can do the task. Whether
a policy can is what v0.3 is for.

Run:  python benchmarks/capture_grasp.py
Needs the sim extra and Pillow. No GPU, no hardware.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

# Grasp point in the Fixed_Jaw frame [m], midway between the four fixed and four moving
# jaw pads. Measured, not guessed — see benchmarks/README.md, section 2.
GRASP_LOCAL = np.array([-0.0087, -0.0823, 0.0])

# Joint targets [rad] for the five arm joints, solved by the IK below against the cube at
# its spawn position. Hardcoded so this script is reproducible without re-solving, and
# checked against the solver on every run.
POSE_HOME = np.array([0.0, -1.57, 1.57, 1.57, -1.57])
POSE_APPROACH = np.array([0.042, -1.550, 1.543, 1.517, -1.567])  # 80mm above the cube
POSE_GRASP = np.array([0.042, -1.164, 1.623, 1.250, -1.550])  # at the cube
POSE_LIFT = np.array([0.042, -1.825, 1.392, 1.607, -1.572])  # 150mm above the cube

# Gripper commands, normalised: 0 closed, 1 open. 0.6 gives a ~58mm gap against a 30mm
# cube — room to descend around it without catching an edge.
JAW_OPEN = 0.6
JAW_SHUT = 0.0

SCENE = Path(__file__).resolve().parents[1] / "src/tendon/assets/scenes/so_arm100_cube.xml"
IMAGES = Path(__file__).resolve().parent / "images"

SUCCESS_HEIGHT_M = 0.1  # skills/grasp/cube-sim/skill.yaml, eval.success.cube_height_above


def grasp_point(model, data, q: np.ndarray, mj) -> np.ndarray:
    """World position of the point the jaws close on, for a given joint vector."""
    data.qpos[:6] = q
    mj.mj_forward(model, data)
    body = data.body("Fixed_Jaw")
    return body.xpos + body.xmat.reshape(3, 3) @ GRASP_LOCAL


def solve_ik(model, data, mj, target: np.ndarray, seed: np.ndarray, jaw: float) -> np.ndarray:
    """Damped least squares over finite differences. Five joints, three-dimensional target.

    Finite differences rather than `mj_jac` because the quantity being differentiated is a
    point offset inside a body frame, not a site MuJoCo knows about. Five extra
    `mj_forward` calls per iteration is nothing here and keeps the scene file unchanged.
    """
    q = np.array(seed[:5], dtype=float)
    low, high = model.jnt_range[:5, 0], model.jnt_range[:5, 1]
    for _ in range(500):
        current = grasp_point(model, data, np.r_[q, jaw], mj)
        error = target - current
        if np.linalg.norm(error) < 1e-4:
            break
        jacobian = np.zeros((3, 5))
        for j in range(5):
            probe = np.zeros(5)
            probe[j] = 1e-5
            jacobian[:, j] = (grasp_point(model, data, np.r_[q + probe, jaw], mj) - current) / 1e-5
        # Damping keeps the step finite near a singular configuration, which this arm
        # reaches whenever the wrist lines up with the shoulder.
        step = jacobian.T @ np.linalg.solve(jacobian @ jacobian.T + 1e-4 * np.eye(3), error)
        q = np.clip(q + 0.5 * step, low, high)
    return q


def label(image: np.ndarray, text: str) -> np.ndarray:
    """Burn a caption into the top-left of a frame.

    In the image rather than in the markdown around it, so a reader who opens the PNG on
    its own still knows which stage they are looking at.
    """
    from PIL import Image, ImageDraw

    pil = Image.fromarray(image)
    draw = ImageDraw.Draw(pil)
    draw.rectangle([0, 0, 8 + 6 * len(text), 16], fill=(0, 0, 0))
    draw.text((4, 3), text, fill=(255, 255, 255))
    return np.asarray(pil)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    args = parser.parse_args()

    import os

    import mujoco

    # MuJoCo cannot open a non-ASCII absolute path on Windows; loading from the scene's own
    # directory keeps every path relative and ASCII. Same reason as drivers/mujoco.py.
    prior = Path.cwd()
    os.chdir(SCENE.parent)
    try:
        model = mujoco.MjModel.from_xml_path(SCENE.name)
    finally:
        os.chdir(prior)

    data = mujoco.MjData(model)
    reset_key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "start")

    # Confirm the hardcoded poses still solve. If the scene moves the cube or changes the
    # arm, this is where it is noticed rather than in a picture of a near miss.
    cube_spawn = np.array([0.0, -0.25, 0.015])
    for name, pose, target in [
        ("approach", POSE_APPROACH, cube_spawn + [0, 0, 0.08]),
        ("grasp", POSE_GRASP, cube_spawn),
        ("lift", POSE_LIFT, cube_spawn + [0, 0, 0.15]),
    ]:
        solved = solve_ik(model, data, mujoco, target, POSE_HOME, JAW_OPEN)
        drift = float(np.max(np.abs(solved - pose)))
        if drift > 0.05:
            print(f"  WARN: hardcoded {name} pose is {drift:.3f} rad from the IK solution")

    renderer = mujoco.Renderer(model, height=args.height, width=args.width)
    mujoco.mj_resetDataKeyframe(model, data, reset_key)
    mujoco.mj_forward(model, data)

    frames: dict[str, list[np.ndarray]] = {"scene": [], "wrist": []}

    def capture(caption: str) -> None:
        for camera in ("scene", "wrist"):
            renderer.update_scene(data, camera=camera)
            frames[camera].append(label(renderer.render(), caption))
        print(f"  {caption:<10} cube z = {data.body('cube').xpos[2]:.4f} m")

    def drive(target: np.ndarray, jaw_from: float, jaw_to: float, steps: int) -> None:
        """Interpolate to a pose over `steps` control periods at 100Hz."""
        origin = np.array(data.ctrl[:5])
        for i in range(steps):
            t = (i + 1) / steps
            data.ctrl[:5] = origin * (1 - t) + target * t
            data.ctrl[5] = jaw_from * (1 - t) + jaw_to * t
            for _ in range(5):  # five 2ms physics steps per 100Hz control step
                mujoco.mj_step(model, data)

    print("\nscripted grasp, 100Hz control:")
    capture("1 start")
    drive(POSE_APPROACH, JAW_OPEN, JAW_OPEN, 80)
    capture("2 approach")
    drive(POSE_GRASP, JAW_OPEN, JAW_OPEN, 80)
    capture("3 descend")
    drive(POSE_GRASP, JAW_OPEN, JAW_SHUT, 60)
    drive(POSE_GRASP, JAW_SHUT, JAW_SHUT, 30)
    capture("4 close")
    drive(POSE_LIFT, JAW_SHUT, JAW_SHUT, 120)
    drive(POSE_LIFT, JAW_SHUT, JAW_SHUT, 60)
    capture("5 lift")

    height_m = float(data.body("cube").xpos[2])
    renderer.close()

    IMAGES.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    for camera, images in frames.items():
        strip = np.concatenate(images, axis=1)
        path = IMAGES / f"grasp_{camera}.png"
        Image.fromarray(strip).save(path)
        print(
            f"  wrote {path.relative_to(Path(__file__).resolve().parents[1])} "
            f"({strip.shape[1]}x{strip.shape[0]})"
        )

    print()
    if height_m > SUCCESS_HEIGHT_M:
        print(
            f"  PASS: cube lifted to {height_m:.4f} m, above the {SUCCESS_HEIGHT_M} m "
            f"success height in skill.yaml."
        )
        return 0
    print(
        f"  FAIL: cube ended at {height_m:.4f} m, below the {SUCCESS_HEIGHT_M} m success "
        f"height. The body cannot do the task the skill declares."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
