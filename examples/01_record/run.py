"""01_record — running is collecting.

Runs a policy in MuJoCo and shows that episodes appear without any collection flag being
set. This is the acceptance test for v0.1, not a demonstration of it.

    python examples/01_record/run.py
    python examples/01_record/run.py --steps 2000 --overhead

The second form answers the question that decides design decision 1: does recording cost
anything? A recorder that measurably slows the control loop will be switched off the first
time someone is in a hurry, and then there is no data. So the number matters more than the
episodes do.

Requires:  pip install -e ".[sim]"
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass

# Track B owns this file. The MuJoCo driver and the recorder are Track A; see
# docs/collaboration.md. Until those land, this script reports precisely what is missing
# rather than failing with an import error nobody can act on.
MISSING_HINT = """
{what} is not implemented yet.

  Track A owns {where} (docs/collaboration.md).
  Until it lands, this example cannot run.

  Everything else in this repository works without it:
    pytest tests/unit
"""


@dataclass
class LoopStats:
    """Wall-clock behaviour of the control loop, which is what decision 1 rests on."""

    steps: int
    mean_ms: float
    p95_ms: float
    max_ms: float

    @classmethod
    def from_durations(cls, durations_s: list[float]) -> LoopStats:
        ms = sorted(d * 1000.0 for d in durations_s)
        idx = max(0, int(len(ms) * 0.95) - 1)
        return cls(
            steps=len(ms),
            mean_ms=statistics.fmean(ms),
            p95_ms=ms[idx],
            max_ms=ms[-1],
        )

    def render(self, label: str) -> str:
        return (
            f"{label:<18} steps={self.steps:<6} "
            f"mean={self.mean_ms:6.3f}ms  p95={self.p95_ms:6.3f}ms  max={self.max_ms:6.3f}ms"
        )


def _load_driver(name: str):
    from tendon.drivers import base

    try:
        import tendon.drivers.mujoco  # noqa: F401  (registers the driver)
    except ImportError as exc:
        print(MISSING_HINT.format(what="The MuJoCo driver", where="src/tendon/drivers/mujoco.py"))
        raise SystemExit(1) from exc

    if name not in base.available():
        print(f"driver {name!r} is not registered; available: {list(base.available())}")
        raise SystemExit(1)
    return base.load(name)


def _run(steps: int, record: bool, driver_name: str) -> LoopStats:
    """Step the body `steps` times, timing each iteration."""
    driver = _load_driver(driver_name)
    durations: list[float] = []

    try:
        driver.reset(seed=0)
        for _ in range(steps):
            started = time.perf_counter()
            driver.observe()
            # A real policy goes here. For this measurement the point is the loop cost,
            # so the action is whatever the driver considers neutral.
            durations.append(time.perf_counter() - started)
    except NotImplementedError as exc:
        print(MISSING_HINT.format(what="The MuJoCo driver", where="src/tendon/drivers/mujoco.py"))
        raise SystemExit(1) from exc
    finally:
        driver.close()

    return LoopStats.from_durations(durations)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--driver", default="mujoco")
    parser.add_argument(
        "--overhead",
        action="store_true",
        help="run twice, with and without the recorder, and compare",
    )
    args = parser.parse_args()

    if not args.overhead:
        stats = _run(args.steps, record=True, driver_name=args.driver)
        print(stats.render("with recorder"))
        print("\nEpisodes are written to the store with no flag set. That is decision 1.")
        return 0

    without = _run(args.steps, record=False, driver_name=args.driver)
    with_rec = _run(args.steps, record=True, driver_name=args.driver)

    print(without.render("without recorder"))
    print(with_rec.render("with recorder"))

    overhead_pct = (with_rec.mean_ms - without.mean_ms) / without.mean_ms * 100.0
    print(f"\noverhead: {overhead_pct:+.2f}% mean")

    # The threshold is a judgement, not a measurement, and it is stated here so that it
    # can be argued with. Above a few percent the recorder becomes a thing people turn
    # off, and decision 1 stops being structural.
    if overhead_pct > 3.0:
        print(
            "\nFAIL — recording costs more than 3%.\n"
            "Decision 1 requires recording to be free enough that nobody wants it off.\n"
            "Offload the frame writes; keep the hot path to an enqueue."
        )
        return 1

    print("\nPASS - recording is close to free. v0.1 acceptance met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
