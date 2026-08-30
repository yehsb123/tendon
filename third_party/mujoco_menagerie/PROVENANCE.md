# Provenance — MuJoCo Menagerie (trs_so_arm100, ufactory_xarm7)

**Source:** https://github.com/google-deepmind/mujoco_menagerie
**Commit:** da76818e269b82289eba39808e2fb91d679d6994
**Retrieved:** 2026-08-30
**Licence:** per model, see below

The Menagerie repository as a whole is `NOASSERTION` on GitHub, because each model carries
the terms of its own author. **Two models are vendored and they are under different
licences**, so the terms have to be read per directory rather than for this folder:

| Model | Licence | Author |
| --- | --- | --- |
| `trs_so_arm100/` | Apache-2.0 | The Robot Studio, via Google DeepMind |
| `ufactory_xarm7/` | BSD-3-Clause | UFACTORY, via Google DeepMind |

Both are permissive and both permit redistribution inside an Apache-2.0 project. BSD-3
additionally requires the copyright notice, the licence text and the disclaimer to be
retained in redistributions, which `ufactory_xarm7/LICENSE` carries unmodified.

`third_party/mujoco_menagerie/LICENSE` is the Apache-2.0 text, kept because CI requires a
`LICENSE` at each `third_party/*` directory. It is **not** the licence of everything
beneath it — the per-model files are.

## What was taken

Sparse checkout of one model directory. The repository holds dozens of robots and is
~570MB; this is 3.1MB of it.

- `trs_so_arm100/` → `third_party/mujoco_menagerie/trs_so_arm100/`
  - `so_arm100.xml` — the MJCF model
  - `scene.xml` — upstream's own scene (ground plane, lighting), kept for reference
  - `assets/*.stl` — 18 meshes, visual and collision
  - `LICENSE`, `README.md`, `CHANGELOG.md`
- `ufactory_xarm7/` → `third_party/mujoco_menagerie/ufactory_xarm7/`, 4.5MB
  - `xarm7.xml`, `hand.xml`, `scene.xml`, `assets/` (16 meshes), `LICENSE`, `README.md`,
    `CHANGELOG.md`
- `trs_so_arm100/LICENSE` → also copied to `third_party/mujoco_menagerie/LICENSE` to
  satisfy the CI check for a licence at each ported directory.

**Why a second arm.** Design decision 3 says a body is a driver. One robot cannot
demonstrate that; it only shows the driver works. The xArm7 disagrees with the SO-ARM100
on every axis the HAL has to absorb — seven joints against five, a **tendon**-driven
gripper against a joint-driven one, 0-255 against radians, and the **opposite end of the
control range means open**. Measured pad separation differs too: 16-104 mm against
7-93 mm. Loading both through the same driver, with the same recorder
producing a different schema for each, is what makes decision 3 checkable rather than
asserted. See `benchmarks/two_bodies.py`.

Upstream in turn derives this MJCF from the URDF published by The Robot Studio at
https://github.com/TheRobotStudio/SO-ARM100 . Their derivation steps are recorded in
`trs_so_arm100/README.md`, kept unmodified for that reason.

## What was changed

**Nothing under `third_party/`.** Every file is byte-identical to upstream.

A derived model lives outside this directory, and is named here so that a reader of either
file finds the other:

- `src/tendon/assets/robots/so_arm100_wrist_cam.xml` — a copy of `so_arm100.xml` with one
  `<camera name="wrist">` added to the `Fixed_Jaw` body, and nothing else. It carries a
  header stating that it is modified, as Apache-2.0 §4(b) requires.
- `src/tendon/assets/robots/xarm7_wrist_cam.xml` — a copy of `xarm7.xml` with one
  `<camera name="wrist">` added to the `xarm_gripper_base_link` body, and nothing else.
  BSD-3 does not require a modification notice the way Apache-2.0 §4(b) does, but the
  header carries one anyway: a reader who finds two nearly identical files should not have
  to diff them to learn which is upstream.

The camera could not be added from the scene file instead. MJCF has no way to add a child
to a body that arrived through `<include>`: re-declaring `<body name="Fixed_Jaw">` fails
with a duplicate name, and `<attach>` composes models without exposing their interiors.
Copying the 130-line model to add one element is the smaller cost.

`<attach>` was tested and rejected for a second reason worth recording: attaching this
model silently drops its `impratio="10"` and `cone="elliptic"` options in favour of the
parent's defaults. Upstream added both specifically for no-slip gripper behaviour
(`README.md`, derivation step 9), so an attached arm grips measurably worse than an
included one, with only a warning on stderr to say so.

## Why this was ported rather than depended on

These are assets, not code, and no Python package ships MJCF and meshes for this arm.
`pip install mujoco` provides the physics engine and no robot models; Menagerie is
distributed only as a git repository. There is nothing to depend on.

Vendoring also keeps the v0.1 promise in `CONTRIBUTING.md` — clone, install, run, with no
GPU and no extra download step. A 3.1MB checkout cost is worth that.

**Why an SO-ARM100 now, when the physical `so101` driver is v0.4 work.** This is the model
the v0.1 MuJoCo driver loads, not the beginning of hardware support. `docs/roadmap.md`
still puts a physical arm at v0.4. The reason to simulate the same body we will later
build for is design decision 3: if the simulated body and the physical body are the same
body under one HAL, the v0.4 driver swap is a driver swap. Simulating a Franka instead
would make it a rewrite.

## Upstream licence obligations

Apache-2.0 requires attribution, the licence text, and notice of modification.

- `LICENSE` copied unmodified, both at this directory and inside `trs_so_arm100/`
- upstream `README.md` and `CHANGELOG.md` kept, since they carry the derivation history
  and the attribution to The Robot Studio
- no file under `third_party/` modified, so no change notice is needed here
- the derived model outside this directory carries its own modification notice

## Update policy

Pinned to the commit above; nothing tracks upstream automatically.

Refresh when the model changes in a way we need — a fix to the jaw collision geometry or
the joint limits would qualify, since both feed `Capability` and the safety limits in
`skills/grasp/cube-sim/skill.yaml`. Cosmetic upstream changes are not a reason to move.

On refresh: re-diff `src/tendon/assets/robots/so_arm100_wrist_cam.xml` against the new
`so_arm100.xml` and re-apply the camera. That file is a one-element patch on purpose, so
the re-apply stays trivial and visible.
