# Concepts

Four decisions. Everything in this repository is downstream of them.

---

## 1. Running is collecting

There is no "data collection mode". Every execution is an episode: observations, actions,
confidence, interventions, outcome. The recorder is always on.

**Why.** Robot data is the hard limit of the field. Roughly a million episodes exist
publicly, against trillions of tokens for language models, because every episode costs
human time at a teleoperation rig. A system where ordinary work produces training data
changes that arithmetic: the data becomes a byproduct of doing the job.

**Consequence.** Storage and curation are first-class, not an afterthought. The recorder
must never be the reason a run is slow, and it must never be switched off.

---

## 2. Human intervention is an interrupt, not an exception

An emergency stop cuts power. Context is destroyed, nothing is recorded, nothing is
learned. It treats the human as a failure handler of last resort.

In tendon, low confidence or a safety trip raises an **interrupt**:

```
policy running
    |  confidence 0.31  ->  INT
    v
context saved  ->  control handed to operator  ->  correction applied
    |
    v  IRET
resume from the saved point, correction recorded as training data
```

**Why.** Policies plateau around 95% success while industry needs 99.9%. That gap is not
closed by a better model alone. It is closed by designing how a human enters and leaves
the loop. Today that design does not exist, so every intervention is wasted.

**Consequence.** Interventions are the most valuable episodes in the store. They are the
only recorded instances of recovering from failure, which demonstration data almost never
contains.

---

## 3. A body is a driver

Policies express intent. Drivers translate intent into whatever a specific body needs.
MuJoCo, an SO-101 arm, and a human demonstration video are all bodies.

**Why.** The same task has a different action space on a two-finger gripper, a five-finger
hand, and a suction cup, so policies do not transfer between robots. Operating systems
solved the equivalent problem for hardware decades ago by putting the difference in a
driver.

**Consequence.** Develop in simulation and deploy to hardware without touching policy code.
And because human video is a read-only driver, human demonstrations land in the same
dataset as robot episodes instead of a separate pipeline.

---

## 4. A skill is a package

A skill is a policy plus its evaluation set, safety limits, and required capabilities:
installable, forkable, publishable.

**Why.** Robot policies are currently distributed as a paper and a GitHub repo, integrated
by hand. Nothing is versioned, comparable, or reusable across sites. Package management is
what turned scattered code into ecosystems everywhere else.

**Consequence.** `tendon fork` on a public skill, plus local corrections, gives a
site-specific variant that can be evaluated against the original. Improvement becomes
measurable.

---

## How the four connect

```
run  --1-->  episodes  --1-->  curated data
 ^                                  |
 |                                  v
 4  skill (versioned)  <--  LoRA fine-tune
 ^                                  ^
 |                                  |
 3  any body                        2  interventions
```

Remove any one and the loop opens. Without 1 there is no data. Without 2 the data lacks
recovery. Without 3 it cannot move between bodies. Without 4 the improvement cannot be
named, shared, or compared.
