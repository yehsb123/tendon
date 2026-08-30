# tendon

**English** · [한국어](README.ko.md)

**The operating layer for physical AI.**
Every run becomes data. Every human correction becomes a lesson.

> A tendon connects muscle to bone. This project connects **policies** to **robots** —
> and puts a human in the loop where the two meet.

---

## The gap

`ROS` has "OS" in its name, but it is a *communication middleware*. It moves messages
between nodes. It has no concept of running an **AI policy** as a managed thing.

That layer does not exist yet:

| A real OS gives you | Robotics today | Missing |
| --- | --- | --- |
| Device drivers | ROS 2 | A **uniform action space** across robot bodies |
| Process management | — | Scheduling **multiple policies / skills** |
| A filesystem | — | Experience as a **first-class, versioned store** |
| System logs | — | **Why** did it move that way |
| A shell | — | A place for a human to **watch and intervene** |
| Interrupts | E-stop (cuts power) | Handing over control **with context preserved** |
| A package manager | — | **Installing and sharing** skills |

`tendon` is that layer.

## Four design decisions

These four are the whole project. Each one negates an assumption the field takes for granted.

### 1. Running *is* collecting
There is no separate "data collection mode". Every execution is recorded as an episode,
curated automatically, and fed back into training. Where `journalctl` keeps logs,
tendon keeps **experience**.

### 2. Human intervention is an interrupt, not an exception
An E-stop cuts power: context is destroyed, nothing is learned. In tendon, low confidence
raises an **interrupt** — control is handed to a human with state preserved, the correction
is recorded, and execution resumes. Intervention becomes a training signal instead of a loss.

### 3. A body is a driver
Policies never address a specific robot. Drivers translate a uniform intent into whatever
the body needs — MuJoCo in simulation, SO-101 on the bench, a human demonstration video.
Develop in sim, deploy to hardware, no policy code changes.

### 4. A skill is a package
```
tendon install  grasp/deformable-bag@1.2
tendon fork     grasp/deformable-bag        # fine-tune on your own site data
tendon eval     grasp/deformable-bag --episodes 50
tendon publish  mysite/bag-handling
```

## What tendon actually builds

**tendon is an orchestration layer, not a reinvention.** The hard parts — simulation,
policy architectures, dataset formats, 3D visualization — are already solved by excellent
open source. tendon writes only the four things nobody else has:

1. **Embodiment HAL** — the driver contract that makes bodies interchangeable
2. **Interrupt protocol** — how control is handed to a human and handed back
3. **Curation metrics** — which episodes help training and which poison it
4. **The shell** — the interface where a human reads intent and intervenes

Everything else is composed.

## Built on

| Layer | We use | Not building |
| --- | --- | --- |
| Robot control & datasets | [LeRobot](https://github.com/huggingface/lerobot) | our own dataset format |
| Simulation | [MuJoCo](https://github.com/google-deepmind/mujoco) | a physics engine |
| Policies | [OpenVLA](https://github.com/openvla/openvla), SmolVLA, GR00T N1.5 | a foundation model |
| Fine-tuning | [PEFT](https://github.com/huggingface/peft) / LoRA | a training framework |
| Visualization | [Rerun](https://github.com/rerun-io/rerun) | a 3D renderer |
| Registry | [Hugging Face Hub](https://huggingface.co/docs/hub) | our own registry |
| Runtime / API | FastAPI, Pydantic | an RPC framework |

## Status

**v0.1 — in development. Simulation only.** Nothing works yet, and nothing here should be
connected to physical hardware: safety enforcement and the interrupt path are unwritten.
See [SECURITY.md](SECURITY.md) before going anywhere near a robot.

The project is proven or discarded at **v0.3**, where a single graph must show:
*after N human corrections, the intervention rate drops.*
Everything after that only matters if that graph exists.
See [docs/roadmap.md](docs/roadmap.md).

## Repository layout

Every directory carries a README stating what belongs in it and what does not. That is
the load-bearing part of the structure, not decoration.

```
src/tendon/
  kernel/        scheduler, action bus, interrupt, safety, and the contracts
                 other layers implement (types.py, protocols.py)
  drivers/       the embodiment HAL — mujoco, lerobot, so101, human video
  services/      recorder, curator, trainer, evaluator, registry
  api/           REST for precomputable state, WebSocket for live intent
  cli/           the tendon command

shell/src/
  views/         Live, Episodes, Skills, Training
  panels/        IntentPreview, ConfidenceMeter, InterruptPrompt, ...
  rerun/         embedded Rerun viewer, clock-aligned to the episode
  api/           typed client mirroring kernel/types.py
  state/         connection, episode, pending decision
  design/        tokens and primitives, sized for a tablet on a factory floor

docs/            concepts, architecture, stack, roadmap, glossary, collaboration
  decisions/     ADRs — one per irreversible choice
skills/          installable capabilities, distributed via the HF Hub
examples/        01_record → 04_improve, ordered by what each one proves
tests/           unit (CPU-only, runs on a bare checkout) + integration
```

## Documentation

| | |
| --- | --- |
| [docs/concepts.md](docs/concepts.md) | the four decisions everything descends from |
| [docs/architecture.md](docs/architecture.md) | layers, two clocks, import rules |
| [docs/stack.md](docs/stack.md) | every dependency, its alternative, its revisit condition |
| [docs/roadmap.md](docs/roadmap.md) | v0.1–v0.4, each with what would kill it |
| [docs/glossary.md](docs/glossary.md) | terms sorted by which field they came from |
| [docs/collaboration.md](docs/collaboration.md) | parallel tracks and file ownership |
| [docs/decisions/](docs/decisions/) | ADRs |
| [CONTRIBUTING.md](CONTRIBUTING.md) | conventions, porting rules, commit format |
| [SECURITY.md](SECURITY.md) | safety first, then software |

## License

Apache-2.0
