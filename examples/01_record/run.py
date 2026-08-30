"""01_record — running is collecting.

Runs a skill in MuJoCo and shows that episodes land in the store with no collection flag
set anywhere. This is the acceptance test for design decision 1, not a demonstration of it.

    python examples/01_record/run.py
    python examples/01_record/run.py --overhead
    python examples/01_record/run.py --root /tmp/scratch-store

Two questions, and the script is only worth running because it answers both by measurement:

**Did anything actually get written?** The store is read back afterwards, through
`services.store`, which reads the disk layout and has never heard of the recorder. A run
that printed "recorded" while writing nothing would be caught by the count not moving.

**Did recording cost anything?** A recorder that measurably slows the control loop gets
switched off the first time somebody is in a hurry, and then there is no data. The verdict
is the share of the control period spent inside the recorder, taken from the bus's own
measurement of its subscribers rather than from the difference between two wall-clock runs
— that difference is mostly noise on a machine doing anything else at the time.

Requires:  pip install -e ".[sim]"
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

#: What the run is recorded as. The scripted policy plays this skill's sequence.
SKILL = "grasp/cube-sim"

#: Above this share of one control period, recording is a thing people turn off, and
#: decision 1 stops being structural. A judgement, not a measurement — stated here as a
#: number so it can be argued with rather than assumed.
BUDGET_LIMIT_PCT = 3.0

MISSING_HINT = """
{what} is not available.

  {where}

  Everything else in this repository works without it:
    pytest tests/unit
"""


@dataclass(frozen=True)
class Run:
    """One episode, and what it cost.

    Separates the two clocks the question is actually about: `per_step_ms` is what the
    loop took, `publish_ms` is the part of that spent inside subscribers. Only the second
    is attributable to recording.
    """

    steps: int
    #: Wall-clock per control step [ms], the whole loop including MuJoCo and the policy.
    per_step_ms: float
    #: Subscriber time per step [ms], measured by the bus. Zero when nothing is attached.
    publish_ms: float
    #: The control period being met [ms].
    period_ms: float
    #: Episodes in the store before and after this run.
    store_before: int
    store_after: int

    @property
    def budget_pct(self) -> float:
        """Share of one control period spent recording."""
        return self.publish_ms / self.period_ms * 100.0

    def render(self, label: str) -> str:
        return (
            f"{label:<18} steps={self.steps:<5} "
            f"loop={self.per_step_ms:6.3f}ms  "
            f"recorder={self.publish_ms:6.3f}ms  "
            f"({self.budget_pct:5.2f}% of a {self.period_ms:.1f}ms period)"
        )


def _episode(*, steps: int, driver_name: str, recording: bool, root: Path) -> Run:
    """Run one episode, optionally with the recorder attached to the scheduler's bus.

    The recorder is a bus subscriber rather than something the scheduler knows about. That
    is what makes decision 1 structural: there is no branch in the control loop to switch
    off, so the difference between recording and not is whether anybody subscribed.
    """
    from tendon.kernel.bus import Bus
    from tendon.kernel.scheduler import Scheduler, StepRecord
    from tendon.kernel.types import SafetyLimits
    from tendon.services.bodies import open_body
    from tendon.services.policy_scripted import ScriptedPolicy

    before = _episodes_in(root)

    body = open_body(driver_name)
    capability = body.capability
    bus: Bus[StepRecord] = Bus()
    recorder = None

    try:
        if recording:
            from tendon.services.recorder import Recorder

            recorder = Recorder(root=root)
            recorder.start(SKILL, capability)
            recorder.attach_to(bus)

        policy = ScriptedPolicy(control_hz=capability.control_hz)
        scheduler = Scheduler(
            driver=body,
            limits=SafetyLimits(max_joint_velocity=4.0),
            bus=bus,
        )

        started = time.perf_counter()
        result = scheduler.run_episode(policy, max_steps=steps, seed=0)
        elapsed = time.perf_counter() - started
    finally:
        if recorder is not None:
            # Inside `finally` because the episode directory stays half-written otherwise,
            # and a half-written dataset is what `store` reports as unreadable later.
            recorder.finish()
        body.close()

    if result.subscriber_failures:
        # A run whose recorder died at step 12 produced 12 steps of data and otherwise
        # looks normal. Saying so here is the difference between noticing now and
        # noticing when the training set turns out to be short.
        for failure in result.subscriber_failures:
            print(f"  subscriber {failure.name} failed at step {failure.step}: {failure.error}")

    steps_run = max(1, result.steps)
    return Run(
        steps=result.steps,
        per_step_ms=elapsed / steps_run * 1000.0,
        publish_ms=bus.mean_publish_cost() * 1000.0,
        period_ms=1000.0 / capability.control_hz,
        store_before=before,
        store_after=_episodes_in(root),
    )


def _episodes_in(root: Path) -> int:
    """Episodes currently in the store, read from the disk layout.

    Deliberately goes through `services.store`, which cannot import the recorder — so this
    count is an independent reading rather than the recorder being asked to confirm its own
    work. Unreadable datasets count as zero episodes, which is the conservative direction:
    it can only make the delta look smaller.
    """
    from tendon.services.store import list_datasets

    return sum(d.episodes or 0 for d in list_datasets(root))


def _report_store(run: Run, root: Path) -> bool:
    """Print what landed in the store. Returns whether anything did."""
    written = run.store_after - run.store_before

    print()
    if written <= 0:
        print("FAIL - the episode ran and the store did not grow.")
        print(f"  {root}")
        print("  Recording is a bus subscriber; if nothing was written, nothing subscribed.")
        return False

    print(f"{written} episode(s) written to {root}")
    print("No flag was passed to record them. That is decision 1.")
    print("  tendon episodes        # the same store, from the CLI")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=600)
    parser.add_argument("--driver", default="mujoco")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="episode store to write to (default: ~/.tendon/episodes)",
    )
    parser.add_argument(
        "--overhead",
        action="store_true",
        help="also run with nothing subscribed, to show the loop cost without a recorder",
    )
    args = parser.parse_args()

    try:
        import tendon.drivers.mujoco  # noqa: F401  (registers the driver)
    except ImportError as exc:
        print(
            MISSING_HINT.format(
                what="MuJoCo",
                where='Install the simulator extra:  pip install -e ".[sim]"',
            )
        )
        raise SystemExit(1) from exc

    try:
        import lerobot  # noqa: F401
    except ImportError as exc:
        # Refusing rather than running without the recorder and reporting a pass. The
        # previous version of this script measured a loop with no recorder in it, compared
        # that to itself, and printed that v0.1 was met.
        print(
            MISSING_HINT.format(
                what="The recorder (LeRobot)",
                where=(
                    'Install the recording extra:  pip install -e ".[robot]"\n'
                    "  Without it there is nothing to measure, so this example cannot pass."
                ),
            )
        )
        raise SystemExit(1) from exc

    from tendon.services.store import DEFAULT_ROOT

    root = args.root if args.root is not None else DEFAULT_ROOT

    recorded = _episode(steps=args.steps, driver_name=args.driver, recording=True, root=root)
    print(recorded.render("with recorder"))

    if args.overhead:
        # Same episode, nothing subscribed. Shown for context rather than used for the
        # verdict: the difference between two wall-clock runs on a shared machine is
        # mostly scheduling noise, and judging decision 1 on it is how a meaningless
        # number gets a PASS printed next to it.
        bare = _episode(steps=args.steps, driver_name=args.driver, recording=False, root=root)
        print(bare.render("nothing attached"))
        print(f"\nwall-clock difference: {recorded.per_step_ms - bare.per_step_ms:+.3f}ms per step")
        print("  Noisy. The verdict below uses the bus measurement instead.")

    if not _report_store(recorded, root):
        return 1

    print()
    print(
        f"recording cost {recorded.publish_ms:.3f}ms per step, "
        f"{recorded.budget_pct:.2f}% of the {recorded.period_ms:.1f}ms control period"
    )

    if recorded.budget_pct > BUDGET_LIMIT_PCT:
        print(
            f"\nFAIL - recording costs more than {BUDGET_LIMIT_PCT:.0f}% of the control period.\n"
            "Decision 1 requires recording to be free enough that nobody wants it off.\n"
            "Offload the frame writes; keep the hot path to an enqueue."
        )
        return 1

    print("\nPASS - recording is close to free, and the episodes are on disk.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
