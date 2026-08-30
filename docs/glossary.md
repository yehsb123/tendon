# Glossary

Terms in this repository arrive from four different fields. This page says which, because
mixing abstraction levels without noticing is the main source of confusion in physical AI.

## Physical AI

**VLA (Vision-Language-Action)** — a model taking images and a language instruction and
emitting actions directly, without explicit perception or planning stages.

**Action chunk** — a short horizon of future actions, typically 0.5 to 1 second, predicted
at once. Reduces compounding error and absorbs inference latency. It is also what makes
intent preview possible: there is a plan to show.

**Embodiment gap** — a policy learned on one body does not transfer to another, because
action spaces differ. Design decision 3 addresses this.

**World model** — a model that predicts the consequence of an action.

**Foundation model** — a model pre-trained broadly enough to be adapted to many tasks
rather than built for one.

**Sim2Real** — transfer of behavior learned in simulation to physical hardware.

**Compounding error** — small deviations push observations outside the training
distribution, where predictions are worse, which deviates further.

**Affordance** — the action an object permits. A handle affords grasping.

## Learning

**Behavior cloning (BC)** — supervised learning from demonstrations.

**Imitation learning** — learning from demonstration, of which BC is the simplest form.

**LoRA** — low-rank adapters that fine-tune a small number of added parameters, leaving base
weights frozen. Small enough to version per site and to ship inside a skill package.

**Teleoperation** — a human directly operating a robot. The main source of demonstrations.

## Robotics

**Proprioception** — the sense a robot has of its own configuration: joint angles,
velocities, gripper state.

**End effector** — the tool at the end of an arm.

**Control rate** — how often the low-level loop runs, typically 100 to 1000Hz. The reason a
large model cannot drive a robot directly, and the reason deliberation and control are
separate tiers in `kernel/scheduler`.

**URDF / USD** — formats describing the links, joints and geometry of a robot.

## tendon

**Body** — anything a driver exposes: a simulator, a physical robot, or recorded human video.

**Skill** — a policy plus its evaluation set, safety limits and required capabilities.

**Interrupt** — a handover of control to a human with context preserved, followed by resume.
Distinct from an E-stop, which cuts power and preserves nothing.

**Curation** — deciding which recorded episodes are worth training on.

**Shell** — the interface where a human reads intent and intervenes.
