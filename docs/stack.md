# Stack — what we compose, and why

tendon is an orchestration layer. The rule is blunt:

> **If an open source project already does it well, we depend on it.
> We write only what does not exist.**

This document records every dependency choice, the alternatives considered, and the
condition under which we would revisit it. A choice without a stated alternative is
not a choice; it is a default nobody examined.

---

## What tendon writes itself

Five things. Everything on this page exists so these five stay small.

It was four until Track A read LeRobot and found that no upstream policy reports
confidence at all — and confidence is what makes design decision 2 fire. The list
grew by one because of what the source said, not because the scope did.

| # | Ours | Why nobody else has it |
| --- | --- | --- |
| 1 | **Embodiment HAL** (`drivers/base.py`) | LeRobot abstracts *robots*; we abstract *bodies*, including human video as a read-only body |
| 2 | **Interrupt protocol** (`kernel/interrupt.py`) | The field has E-stop (cuts power) and teleop (full manual). Nothing in between preserves context |
| 3 | **Curation metrics** (`services/curator.py`) | Everyone collects; nobody agrees on what makes an episode worth training on |
| 4 | **The shell** (`shell/`) | Robotics researchers do not build interfaces; interface designers do not know robotics |
| 5 | **Confidence estimation** (`services/`) | `PreTrainedPolicy` returns a bare action tensor; no policy reports how sure it is. See [ADR 0003](decisions/0003-confidence-has-no-upstream-source.md) |

---

## Simulation

**MuJoCo** — Apache-2.0, DeepMind.

The default and the only simulator v0.1 supports. Chosen over Isaac Sim for three reasons:
it installs with `pip` on any machine, its contact physics is the most trusted in the
manipulation literature, and it runs without an NVIDIA GPU. Development must not require
a workstation.

*Alternative considered:* **Isaac Sim / Isaac Lab** — better photorealistic rendering and
massively parallel RL, both of which matter later. Rejected for v0.1 and revisited in
[ADR 0002](decisions/0002-isaac-lab-is-a-later-driver.md) against the actual 3.0.0-beta2
source. Short version: 441k LOC, 15 packages, Python 3.12 and torch 2.10 pinned exactly,
RTX required, Linux-first. It becomes a driver when we need vision-based Sim2Real or
large-scale RL, not before.

*Correction:* an earlier draft of this file listed **Newton** as something to watch for.
It has already shipped — Isaac Lab 3.0 contains `isaaclab_newton` as one of three
swappable physics backends, with MuJoCo-Warp as its default solver, Warp-based
differentiability, and kit-less execution that does not require Isaac Sim at all. It is
still beta and carries no official support, and precision-assembly tasks (Factory, Forge,
AutoMate) remain PhysX-only. So the tradeoff has narrowed rather than disappeared, and
the revisit condition is now concrete: **when Newton kit-less leaves beta.** At that point
the Isaac driver costs a GPU rather than the whole Omniverse stack.

## Robot control and dataset format

**LeRobot** — Apache-2.0, Hugging Face.

The single most important dependency. It gives us hardware drivers (SO-100/101 and more),
the `LeRobotDataset` format, and reference policy implementations. We wrap it as a driver
rather than competing with it.

**We do not define our own dataset format.** Episodes are written as `LeRobotDataset`
(parquet + mp4) so that anything recorded by tendon is trainable by the wider ecosystem
on day one, and anything published on the Hub is replayable by tendon. Interoperability
is worth more than a format tuned to us.

*Alternative considered:* **RLDS / TFDS** (used by Open X-Embodiment). Well-specified, but
TensorFlow-centric and heavier to write incrementally during live execution. We read RLDS
through conversion; we write LeRobot.

*Alternative considered:* a custom format. Rejected — a new format is a new island.

## Policies

We ship no foundation model. Skills reference open weights.

| Policy | License | Role |
| --- | --- | --- |
| **SmolVLA** | Apache-2.0 | Default. Small enough to fine-tune and serve on one consumer GPU |
| **OpenVLA** | MIT | 7B reference point when quality matters more than latency |
| **ACT / Diffusion Policy** | via LeRobot | Strong single-task baselines for evaluation |
| **GR00T N1.5** | NVIDIA open weights | Humanoid bodies, once such a driver exists |

The default is deliberately the *small* model. A loop that closes overnight on one RTX card
beats a better model that needs a cluster, because design decision 1 requires the loop to
close every night.

## Fine-tuning

**PEFT (LoRA)** + **transformers** + **accelerate** — Apache-2.0, Hugging Face.

Full fine-tuning is out of reach for a single GPU and would break the nightly loop. LoRA
adapters are small enough to version per site, per skill, and to ship as part of a skill
package.

*Alternative considered:* full fine-tuning, and RL from interventions. Both are later work;
neither fits the overnight constraint that makes the loop real.

## Visualization

**Rerun** — Apache-2.0/MIT, rerun.io.

3D scenes, trajectories and time-aligned sensor streams, with a web viewer we embed
directly in the shell. Building a 3D renderer would consume the entire project budget and
produce something worse.

**What Rerun does not do is exactly what the shell is for.** Rerun *shows* data. It has no
notion of pending intent, confidence, approval, or handover. We embed Rerun for the view
and build the decision surface on top.

## Experience store and query

**DuckDB** — MIT.

Episode metadata, curation scores, intervention records. Embedded, zero-ops, reads parquet
natively — which is what `LeRobotDataset` already writes. No server for a single-host runtime.

*Alternative considered:* SQLite (weaker analytical queries over parquet), Postgres
(operational burden a robot host should not carry).

## Distribution

**Hugging Face Hub** — `huggingface_hub`, Apache-2.0.

Skills, weights, LoRA adapters and datasets are all Hub artifacts. `skill.yaml` points at a
Hub repo; `tendon install` resolves it. **We do not run a registry.** Running one means
running auth, storage, moderation and uptime — none of which is this project.

## Runtime and API

**FastAPI**, **Pydantic v2**, **uvicorn**, **Typer**, **Rich** — all MIT/Apache-2.0.

Pydantic models are the contract between kernel, drivers and shell. Because actions and
observations are typed at the boundary, a driver that violates the HAL fails loudly at the
edge rather than silently mid-episode.

*Deliberately not used:* **ROS 2** as a hard dependency. It is the right answer for
distributed industrial deployments and will arrive as a driver. Requiring it for v0.1 would
force every contributor through a heavy install to run a simulated arm.

## The shell

**React + TypeScript + Vite**, **@rerun-io/web-viewer**, **three.js / react-three-fiber**
for the intent preview overlay.

Rerun renders the scene; the overlay renders what has not happened yet — proposed
trajectory, target, confidence, alternatives. That overlay is the part no library provides.

---

## Standing rule

Before writing a module, search for the project that already does it. Record what you found
in `docs/decisions/` even when you decide to write it anyway — especially then.

A dependency is added when it removes more code than it adds. A dependency is questioned
when it constrains one of the five things above.
