"""Does the curator actually separate good episodes from bad ones?

`docs/stack.md` lists curation metrics as one of the five things tendon writes itself, and
`services/curator.py` says why it carries the burden of proof: it is the only module that
fails *quietly*. A metric that mislabels a good episode removes it from training, the
policy gets slightly worse, and nothing goes red.

Unit tests can show a signal responds to a synthetic input. They cannot show it separates
real episodes, because a unit test writes both the episode and the expectation. So this
collects actual runs — same body, same scheduler, same recorder — degrades some of them in
specific ways, reads them back off disk, and checks the ranking comes out right.

The read-back matters. Scores are computed from actions recovered through
`drivers/human.py`, not from the in-memory objects that produced them. What a curator sees
in production is a dataset, not a run.

    ScriptedPolicy (+ a defect) -> Scheduler -> Recorder -> LeRobotDataset
                                                                  |
                                       curator.score_episode <- HumanDriver

Exits non-zero if a degraded episode outranks a clean one.

Run:  python benchmarks/curation.py
Needs the sim and robot extras. No GPU, no hardware.
"""

from __future__ import annotations

import argparse
import random
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import tendon.drivers.human  # noqa: F401  registers the driver
import tendon.drivers.mujoco  # noqa: F401  registers the driver
from tendon.drivers.base import load
from tendon.kernel.bus import Bus
from tendon.kernel.scheduler import Scheduler, StepRecord
from tendon.kernel.types import Action, ActionSpace, Intent, Observation, SafetyLimits
from tendon.services.curator import (
    ScoredEpisode,
    median_steps,
    score_episode,
    select,
    signals_for,
)
from tendon.services.policy_scripted import ScriptedPolicy
from tendon.services.recorder import Recorder

CONTROL_HZ = 100.0
LIMITS = SafetyLimits(max_joint_velocity=50.0)  # wide, so defects are not clamped away


# --------------------------------------------------------------------------- defects


class Defect:
    """Wraps a policy and damages its output in one specific way.

    Deliberately crude. Each defect targets exactly one curator signal, so a ranking that
    comes out wrong says which signal failed rather than that something, somewhere, is
    off.
    """

    name = "clean"
    description = "no defect"

    def __init__(self, inner: ScriptedPolicy, rng: random.Random) -> None:
        self._inner = inner
        self._rng = rng

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        return self._inner.requires

    def reset(self) -> None:
        self._inner.reset()

    def predict(self, observation: Observation) -> Intent:
        return self._inner.predict(observation)

    def _rebuild(self, intent: Intent, actions: list[Action]) -> Intent:
        return Intent(
            horizon_s=intent.horizon_s,
            actions=tuple(actions),
            confidence=intent.confidence,
            goal=intent.goal,
            target=intent.target,
        )


class Jittery(Defect):
    """Adds noise to every joint target. Targets `peak_jerk`.

    What this stands in for: a teleoperation rig with a loose encoder, a dropped frame in a
    demonstration, or two controllers fighting each other. All produce a trajectory that
    reaches the right place by a route no one would choose.
    """

    name = "jittery"
    description = "noise on every joint target"

    def predict(self, observation: Observation) -> Intent:
        intent = self._inner.predict(observation)
        damaged = [
            Action(
                space=a.space,
                values=[v + self._rng.gauss(0.0, 0.05) for v in a.values],  # [rad]
                gripper=a.gripper,
            )
            for a in intent.actions
        ]
        return self._rebuild(intent, damaged)


class Idle(Defect):
    """Freezes for most of the chunk. Targets `idle_fraction`.

    What this stands in for: an operator hesitating, or a recording that kept running after
    the task finished. Both fill a dataset with steps that teach nothing.
    """

    name = "idle"
    description = "holds position for most of each chunk"

    def predict(self, observation: Observation) -> Intent:
        intent = self._inner.predict(observation)
        first = intent.actions[0]
        held = [first] * (len(intent.actions) - 1) + [intent.actions[-1]]
        return self._rebuild(intent, held)


class Churny(Defect):
    """Toggles the gripper every step. Targets `gripper_churn`.

    What this stands in for: grip fighting at contact, or an operator unsure whether the
    object is held. The arm may still complete the task, which is what makes this worth
    detecting separately from failure.
    """

    name = "churny"
    description = "gripper toggles every step"

    def predict(self, observation: Observation) -> Intent:
        intent = self._inner.predict(observation)
        damaged = [
            Action(space=a.space, values=list(a.values), gripper=float(i % 2))
            for i, a in enumerate(intent.actions)
        ]
        return self._rebuild(intent, damaged)


# Three clean runs against one of each defect. The proportion matters: `score_episode`
# scales jerk against the population median, so a population that is mostly defective drags
# the reference up until nothing looks unusual. Measured directly — with one clean episode
# in four, the median peak jerk came out at 534,308 and the jittery episode triggered no
# reason at all, because it was being compared against itself.
#
# Real collection is mostly-good with some bad, and the curator has to be evaluated in the
# regime it will run in.
DEFECTS: tuple[type[Defect], ...] = (Defect, Defect, Defect, Jittery, Idle, Churny)


# ------------------------------------------------------------------------ collection


@dataclass
class Collected:
    name: str
    description: str
    episode_index: int
    steps: int


def collect(root: Path, seed: int) -> list[Collected]:
    """Record one episode per defect into a single dataset.

    One dataset, not one per defect: a curator ranks episodes against each other, and the
    reference scales it uses — median steps, median jerk — only mean something across a
    population.
    """
    driver = load("mujoco", control_hz=CONTROL_HZ)
    recorder = Recorder(root=root, use_videos=False)
    collected: list[Collected] = []

    try:
        for index, defect_class in enumerate(DEFECTS):
            rng = random.Random(seed + index)
            policy = defect_class(ScriptedPolicy(control_hz=CONTROL_HZ), rng)

            label = f"{defect_class.name}-{index}"
            recorder.start(f"scripted/{defect_class.name}", driver.capability)
            bus: Bus[StepRecord] = Bus()
            recorder.attach_to(bus)

            steps = policy._inner.plan_steps
            result = Scheduler(driver=driver, limits=LIMITS, bus=bus).run_episode(
                policy, max_steps=steps
            )
            meta = recorder.finish(success=None)
            collected.append(Collected(label, defect_class.description, index, meta.steps))
            print(f"  recorded {label:<10} {result.steps} steps")
    finally:
        driver.close()

    return collected


def actions_from_dataset(root: Path, episode: int) -> list[Action]:
    """Recover an episode's actions by replaying it as a body.

    Through the HAL rather than by reading parquet directly. A curator in production reads
    a dataset, and this is the path that proves the dataset is readable — the same round
    trip `end_to_end.py` checks, used for something.
    """
    replay = load("human", repo_id="tendon/local", root=root, episode=episode)
    try:
        replay.reset()
        actions = [replay.recorded_action()]
        while replay.advance():
            actions.append(replay.recorded_action())
        return actions
    finally:
        replay.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="tendon-curation-"))
    try:
        print("\ncollecting one episode per defect:")
        collected = collect(root, args.seed)

        dataset_root = root / "tendon__local"
        episodes = {c.name: actions_from_dataset(dataset_root, c.episode_index) for c in collected}

        dt_s = 1.0 / CONTROL_HZ
        median = median_steps([len(a) for a in episodes.values()])

        # Population scale for jerk, as `score_episode` asks for: the median across this
        # skill on this body. Using an absolute number would score the hardware.
        from tendon.services.curator import peak_jerk

        jerks = sorted(peak_jerk(a, dt_s) for a in episodes.values())
        jerk_reference = jerks[len(jerks) // 2]

        print(
            f"\npopulation: {len(episodes)} episodes, median {median:.0f} steps, "
            f"median peak jerk {jerk_reference:.1f}"
        )
        print("\nscores:")

        scored: list[ScoredEpisode] = []
        table: list[tuple[str, float, tuple[str, ...]]] = []
        for c in collected:
            actions = episodes[c.name]
            signals = signals_for(actions, dt_s, median)
            score, reasons = score_episode(signals, jerk_reference=jerk_reference)
            scored.append(
                ScoredEpisode(episode_id=c.name, score=score, signals=signals, reasons=reasons)
            )
            table.append((c.name, score, reasons))

        for name, score, reasons in sorted(table, key=lambda r: -r[1]):
            print(f"  {name:<8} {score:.3f}")
            for reason in reasons:
                print(f"           - {reason}")

        ranking = [s.episode_id for s in select(scored)]
        print(f"\nranking: {' > '.join(ranking)}")

        clean_scores = [s.score for s in scored if s.episode_id.startswith("clean")]
        worst_clean = min(clean_scores)

        failures = []
        for s in scored:
            if s.episode_id.startswith("clean"):
                continue
            if s.score >= worst_clean:
                failures.append(
                    f"{s.episode_id} scored {s.score:.3f}, at or above the worst clean "
                    f"episode at {worst_clean:.3f}"
                )
            if not s.reasons:
                # A score with no reason is unusable: a reviewer cannot disagree with it.
                failures.append(f"{s.episode_id} was penalised but gave no reason")

        spread = max(clean_scores) - min(clean_scores)
        print(
            f"clean episodes span {spread:.3f} ({min(clean_scores):.3f} to {max(clean_scores):.3f})"
        )

        print()
        if failures:
            for line in failures:
                print(f"  FAIL: {line}")
            return 1
        print("  PASS: every degraded episode ranks below every clean one, with reasons.")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
