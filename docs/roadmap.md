# Roadmap

The project is proven or discarded at **v0.3**. Everything before it exists to make that
test reachable. Everything after it only matters if the test passes.

---

## v0.1 — Running is collecting  *(~4 weeks)*

MuJoCo driver, kernel loop, recorder, episode store, CLI.

**Done when:** `tendon run` executes a policy in simulation and episodes appear in
LeRobotDataset format without any collection flag being set.

**Kills the milestone:** the recorder measurably slows the control loop. If recording is a
cost, it will be switched off, and decision 1 fails.

## v0.2 — Intent before motion  *(~4 weeks)*

Shell, intent preview, approve and reject.

**Done when:** an operator sees the upcoming trajectory, target and confidence *before*
execution, and can reject it.

**Kills the milestone:** the action chunk turns out not to be legible to a human. If what a
policy plans cannot be rendered as something an operator judges in a few seconds, the
premise of the shell is wrong and the project should stop here.

## v0.3 — Correction becomes learning  *(~6 weeks)*  — the proof

Interrupt protocol, correction recording, curation, nightly LoRA.

**Done when one graph exists:**

> x-axis: cumulative human corrections.  y-axis: intervention rate.
> The line goes down.

That graph is the entire claim of this project. If it is flat, the loop does not close, and
no amount of additional engineering fixes it.

## v0.4 — Bodies and packages  *(~6 weeks)*

SO-101 driver, skill package format, install, fork, publish.

**Done when:** a skill trained in simulation runs on physical hardware with no policy code
change, and a forked skill can be evaluated against its parent.

---

## Explicitly out of scope for now

| | Why | Revisit when |
| --- | --- | --- |
| Multi-robot fleets | A single host is hard enough | v0.4 ships |
| Isaac Sim driver | MuJoCo covers v0.1 through v0.3 | vision-based Sim2Real is needed |
| ROS 2 driver | Install burden on contributors | a real industrial deployment asks |
| Training a foundation model | Not a solo problem | never |
| Our own registry | The Hub is sufficient | never, ideally |
