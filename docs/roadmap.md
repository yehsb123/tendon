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

**Where it stands.** Trajectory, target and confidence all render before execution, and an
operator can approve, reject or correct. What is not yet answered is the part that decides
the milestone: whether the drawing is *legible*. That is a question about a person, and no
test settles it — someone has to sit in front of it with an episode running. Until then
v0.2 is built but not accepted.

## v0.3 — Correction becomes learning  *(~6 weeks)*  — the proof

Interrupt protocol, correction recording, curation, nightly LoRA.

**Done when one graph exists:**

> x-axis: cumulative human corrections.  y-axis: intervention rate.
> The line goes down.

That graph is the entire claim of this project. If it is flat, the loop does not close, and
no amount of additional engineering fixes it.

**Where it stands.** The line goes down, and the running system draws it. An operator
corrects a motion in the shell; the correction reaches the policy, the episode's
`interrupts` table and a memory that outlives both the episode and the process; and
`Progress` — or `tendon progress` — plots the intervention rate against corrections taught.
`tests/integration/test_shell_loop_closes.py` runs the same episodes twice, correcting in
one arm and only approving in the other, so the fall is attributable to the teaching rather
than to anything else. Curation ranks recorded episodes with its reasons, in the shell and
on the command line.

**What is not done.** The learner is instance-based: a correction is recalled when the body
is near where it was given. That demonstrates the loop and it is not the *nightly LoRA* this
milestone names — `services/trainer.py` exists and is not wired, and `tendon train` says so.
Nor is confidence calibrated across skills (ADR 0003). So the graph is real and the
mechanism behind it is the simplest one that could produce it, which is worth saying plainly
rather than leaving somebody to discover.

## v0.4 — Bodies and packages  *(~6 weeks)*

SO-101 driver, skill package format, install, fork, publish.

**Done when:** a skill trained in simulation runs on physical hardware with no policy code
change, and a forked skill can be evaluated against its parent.

---

## Explicitly out of scope for now

| | Why | Revisit when |
| --- | --- | --- |
| Multi-robot fleets | A single host is hard enough | v0.4 ships |
| Isaac Lab driver | 441k LOC, pinned toolchain, RTX-only (ADR 0002) | Newton kit-less leaves beta |
| ROS 2 driver | Install burden on contributors | a real industrial deployment asks |
| Training a foundation model | Not a solo problem | never |
| Our own registry | The Hub is sufficient | never, ideally |
