# tendon

**English** · [한국어](README.ko.md)

**The operating layer for physical AI.**
Every run becomes data. Every human correction becomes a lesson.

> A tendon connects muscle to bone. This project connects **policies** to **robots** —
> and puts a human in the loop where the two meet.

---

## It runs

```
$ python examples/04_improve/run.py

intervention rate over a trailing window of 10 episodes

100% │█
 88% │█████
 75% │████████
 62% │██████████ █
 50% │█████████████
 38% │████████████████
 25% │████████████████████████  ███                    ██
 12% │████████████████████████████████████         ██████
     └───────────────────────────────────────────────────
      0                                            52 corrections

  first 10 episodes : 100% interrupted
  last  10 episodes :  20% interrupted
  corrections stored : 52
```

*Figures from one run at the default settings.* The shape is what is tested
([`tests/integration/test_improve_example.py`](tests/integration/test_improve_example.py)):
the rate falls, corrections accumulate, and the policy asks for help before it stops
asking. Pinning the exact numbers would make the test a check on a random seed.

A policy runs on an SO-ARM100 in MuJoCo. Where it is uncertain, confidence falls, and the
scheduler hands over **before the body moves** rather than after something goes wrong. An
operator corrects it. The correction is stored against the situation it was given in, and
later episodes recall it and do not ask again.

Every piece in that loop is the real one — the same scheduler, safety check, interrupt
state machine and evaluator a trained policy would run under.

**What this does not show.** The learner here remembers rather than generalises, and the
operator is scripted. Swapping in LoRA fine-tuning ([`services/trainer.py`](src/tendon/services/trainer.py))
and a human is the v0.3 experiment. This is v0.1 demonstrating that the machinery closes
the loop — stated plainly, because a demo that blurred the two would be answering a
question nobody asked while appearing to answer the one that matters.

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

### 2. The policy raises its own hand
An E-stop cuts power: context is destroyed, nothing is learned. LeRobot's DAgger strategy
does better — a human takes over mid-policy and the correction is recorded — but **every
handover there is started by a person who is already watching.**

In tendon the *policy* asks. Low confidence raises an **interrupt**: control is handed
over with state preserved, the correction is recorded, execution resumes. That is the
difference between supervision that scales and supervision that needs one operator per
robot. See [ADR 0004](docs/decisions/0004-lerobot-already-does-half-of-this.md).

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
open source. tendon writes only the five things nobody else has:

1. **Embodiment HAL** — the driver contract that makes bodies interchangeable
2. **Interrupt protocol** — how control is handed to a human and handed back
3. **Curation metrics** — which episodes help training and which poison it
4. **The shell** — the interface where a human reads intent and intervenes
5. **Confidence estimation** — no upstream policy reports how sure it is, and that
   number is what makes intervention fire ([ADR 0003](docs/decisions/0003-confidence-has-no-upstream-source.md))

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

## Quick start

Nothing here needs a GPU, a robot, or a simulator.

```bash
git clone https://github.com/yehsb123/tendon.git
cd tendon
python -m pip install -e ".[dev]"
pytest tests/unit
```

Watch an episode and intervene in it:

```bash
cd shell && npm install && npm run build && cd ..
python -m pip install -e ".[sim]"
tendon serve                               # http://127.0.0.1:8000
```

One command serves both the runtime and the interface. Start an episode from the page; the
trajectory updates as the body moves, and when the policy is unsure it hands over and waits
for you.

The episode is recorded on the same terms as one started from the command line, into the
same dataset — an episode is an episode, whichever way it was begun, and `Episodes` shows
it when the run ends.

A correction you make goes three places: into the motion, into the policy's memory, and
into the episode's `interrupts` table. The third is the one that matters later —
demonstration data almost never contains recovery from failure, and that table is the only
place it is written down.

While working on the shell itself, run it against a live runtime instead — the dev server
reloads on edit and proxies `/api` and `/ws` through:

```bash
tendon serve                  # terminal one
cd shell && npm run dev       # terminal two, http://localhost:5273
```

Adding the simulator and the recorder:

```bash
python -m pip install -e ".[sim,robot,dev]"
python examples/01_record/run.py
```

That runs a grasp in MuJoCo, then reads the store back through a module that cannot import
the recorder, and reports what recording cost:

```
with recorder      steps=400   loop= 0.309ms  recorder= 0.027ms  ( 0.27% of a 10.0ms period)
1 episode(s) written to ~/.tendon/episodes
```

Recording takes 0.27% of the control period, so nothing is gained by switching it off —
which is the whole of design decision 1. The example fails rather than passes when the
store does not grow, and refuses to run at all without the recorder installed.

## Commands

```
tendon doctor            what works here, and what each missing piece costs
tendon run <skill>       run a policy on a body, and record it
tendon eval <skill>      run it repeatedly, record it, and report what happened
tendon episodes          list what has been recorded
tendon serve             the runtime API, and the shell when it is built
tendon shell             the same, with instructions for the dev server
tendon curate <skill>    v0.3 — not available yet, and says so
tendon train <skill>     v0.3 — not available yet, and says so
```

A skill is named the way everything else names it — `tendon run grasp/cube-sim` — and a
path to a skill directory works too.

```
$ tendon run grasp/cube-sim
grasp/cube-sim 0.1.0 on so_arm100_cube (5 axes, 100 Hz) via scripted
episode        e710613da75f
steps          500
recorded to    ~/.tendon/episodes

$ tendon episodes
skill           episodes      size  last written
grasp/cube-sim         1  585.0 KB  2026-08-31 02:29
```

There is no flag asking for that recording and none to turn it off. `tendon eval` collects
on the same terms, one dataset episode per evaluation episode — it is the command that
produces thirty runs, so it is where most of the data comes from.

If the recorder cannot run — LeRobot is an optional extra — the command says so and keeps
going rather than pretending; if it starts and then fails, the command exits non-zero,
because a run that collected nothing should not report success.

A body that moves real hardware is refused unless you pass `--physical`, and `doctor` says
which bodies those are. Driver arguments go through `--driver-arg key=value`.

## Status

**v0.1 — simulation only.** The loop above runs end to end: the scheduler routes every
action through `kernel/safety`, raises interrupts on low confidence, and applies operator
corrections under the same limits as policy actions.

**Still do not connect this to physical hardware.** A driver for the SO-101 exists, which
makes this warning load-bearing rather than theoretical: nothing here has been verified
against a real body, every safety limit has only ever held in simulation, and there is no
authentication between the shell and the runtime. See [SECURITY.md](SECURITY.md) before
going anywhere near a robot.

The project is proven or discarded at **v0.3**, where a single graph must show:
*after N human corrections, the intervention rate drops.*
Everything after that only matters if that graph exists.
See [docs/roadmap.md](docs/roadmap.md).

## Repository layout

Every directory carries a README stating what belongs in it and what does not. That is
the load-bearing part of the structure, not decoration.

```
src/tendon/
  kernel/        scheduler, step bus, interrupt, safety, and the contracts
                 other layers implement (types.py, protocols.py)
  drivers/       the embodiment HAL — mujoco, human video, more to come
  services/      recorder, curator, confidence, adaptive, evaluator,
                 policies, skill, bodies, trainer, registry
  api/           REST, WebSocket, and the session that bridges the two
  cli/           doctor, run, eval, serve, shell

shell/src/
  views/         Live, Episodes, Skills, Training
  panels/        IntentPreview.tsx — the centre; fixed grid so nothing shifts
  rerun/         embedded Rerun viewer, clock-aligned to the episode
  api/           typed client mirroring kernel/types.py
  state/         connection, episode, pending decision
  design/        tokens.css + app.css — light and dark, sized for a floor tablet

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
