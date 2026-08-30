# ADR 0002 — Isaac Lab is a later driver, not a v0.1 dependency

**Status:** accepted
**Date:** 2026-08-30
**Evidence:** direct reading of `isaac-sim/IsaacLab` at `release/3.0.0-beta2` (commit `bffdce9`)

---

## Context

Isaac Lab is the obvious thing to build on: it is the field standard for GPU-parallel
robot learning, it has 271 registered environments, and NVIDIA maintains it. The question
is not whether it is good but whether depending on it now serves this project.

The 3.0.0-beta2 source was read directly rather than reasoned about from documentation.
What it actually contains:

| | |
| --- | --- |
| Scale | 1,962 Python files, 441,750 LOC, 15 pip packages in one monorepo |
| Version coupling | Isaac Sim 6.0.0/6.0.1, Python `>=3.12,<3.13`, torch 2.10.0 — all pinned |
| Hardware | RTX GPU required for the renderer |
| Platform | Linux-first; Windows checkout fails on long paths |
| Physics backends | PhysX (stable), **Newton** (beta), OvPhysX (highly experimental) |
| License | BSD-3 core, Apache-2.0 for `isaaclab_mimic` (which depends on proprietary cuRobo) |

Two findings changed the picture from the earlier draft of `docs/stack.md`.

**Newton already shipped.** It is not a future project to watch — `isaaclab_newton` is a
112-file package inside 3.0, using MuJoCo-Warp as its default solver, differentiable
through Warp, and — the part that matters here — **runnable without Isaac Sim**. The
README states kit-less Newton workflows do not require it. That removes the proprietary
Omniverse stack from the dependency, though not the GPU.

**The backend factory is the same problem tendon solves one layer up.** `FactoryBase`
intercepts construction, reads the active backend from the simulation context, and
dynamically imports the mirrored implementation, so user import paths never change while
the physics engine underneath is swapped. That is the Embodiment HAL pattern applied to
*physics engines* rather than to *bodies*. Worth studying; not worth depending on.

## What Isaac Lab already covers, and what it does not

This mattered more than the install cost, because overlap would mean tendon has no reason
to exist.

**Covered by Isaac Lab:** simulation, massively parallel RL, teleoperated demo collection
(`isaaclab_teleop`, including Vision Pro via OpenXR/CloudXR), automatic demo augmentation
(`isaaclab_mimic`), demo recording hooks (`RecorderManager`), and a Rerun viewer among its
visualizers — the same rendering choice tendon made independently.

**Not covered:**

1. **Intervention during execution.** `isaaclab_teleop` is for *collecting demonstrations*:
   a human drives the robot to produce data. There is no notion of a policy running, losing
   confidence, handing control to a human with context preserved, and resuming. That is
   design decision 2, and nothing in 441k lines addresses it.
2. **Intent preview.** `RecorderManager` records what happened. Nothing renders what is
   *about to* happen for a human to approve or reject.
3. **Curation judgement.** `isaaclab_mimic` generates more data from few demonstrations.
   Deciding which recorded episodes are worth training on is a different question and is
   not asked.
4. **Skill packaging.** Environments are registered as Gym IDs inside the monorepo. There
   is no install, fork, evaluate-against-parent, or publish.

The official guidance is also telling: `docs/source/overview/own-project/template.rst`
tells users **not to fork the repo** but to build an external project against it. Isaac Lab
positions itself as a substrate, which is compatible with becoming a tendon driver later.

## Decision

**v0.1 through v0.3 use MuJoCo only.** Isaac Lab does not enter the dependency set.

**Isaac becomes a driver at v0.4 or later**, and specifically the **Newton kit-less** path
rather than the full Isaac Sim stack, when Newton leaves beta. Its documentation currently
states that no official support or debugging assistance is provided before release, which
is not a foundation to put a runtime on.

The trigger to revisit, stated concretely so it is checkable: **Newton kit-less reaches a
stable release.** At that point the cost of an Isaac driver is a GPU, not the Omniverse
stack, and the benefit — photorealistic rendering for vision-based Sim2Real, and thousands
of parallel environments — becomes worth paying for.

## Consequences

A contributor can run everything on a laptop through v0.3, which is the condition that
makes the project reachable by one person.

We give up parallel RL and photorealistic rendering for now. Neither is needed to answer
the question v0.3 asks — whether human corrections reduce the intervention rate — because
that question is about the loop closing, not about visual fidelity.

When the Isaac driver is written, `drivers/base.py` must accommodate a body whose
underlying physics backend is itself swappable. The HAL contract in
`kernel/protocols.py` should be checked against that case before v0.4 rather than
discovering the mismatch during implementation.

Two things are worth borrowing before then, independent of the dependency decision:

- **`ActuatorNetLSTM` / `ActuatorNetMLP`** — neural actuator models trained on real motor
  response, and a large part of why ANYmal Sim2Real worked. Relevant to any tendon driver
  that has to match a real body.
- **The `weight=0.0` reward term convention** — a metric computed and logged every step
  while contributing nothing to the objective. The intervention rate should be tracked
  exactly this way.
