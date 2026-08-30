"""04_improve — does correcting a policy reduce how often it has to be corrected?

This is the whole project in one script. Everything else exists to make this runnable.

    for each episode:
        policy proposes an action chunk, with a confidence
        confidence drops in a region the policy is unsure about
        the scheduler raises an interrupt before the body moves
        an operator supplies a correction
        the correction is stored against the situation it was given in
        later episodes recall it, and do not interrupt there again

    then: plot cumulative corrections against intervention rate

    python examples/04_improve/run.py
    python examples/04_improve/run.py --episodes 80 --out results/

Requires:  pip install -e ".[sim]"

## What this does and does not prove

**Proves:** the loop closes. Confidence is measured rather than asserted, an interrupt is
raised before motion rather than after a failure, a human decision is recorded, and later
behaviour differs because of it. Every piece is the real one — the same scheduler, safety
check, interrupt machine and evaluator that a real policy would run under.

**Does not prove:** that a VLA fine-tuned on these corrections gets better. The learner
here remembers rather than generalises (see `services/adaptive.py`), and the operator is
scripted rather than human. Swapping in `services/trainer.py` and a real operator is the
v0.3 experiment; this is the v0.1 demonstration that the machinery works.

Stating that difference is not modesty. A demo that blurs it would be answering a question
nobody asked while appearing to answer the one that matters.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------- the setup

#: Where the policy is unsure, as several distinct situations rather than one.
#:
#: A single uncertain region would be corrected once and never again, which produces a
#: graph with two points in it and proves less than it appears to. Real uncertainty is
#: scattered: an unfamiliar object here, a bad viewing angle there, and a correction for
#: one does not transfer to the others. Several regions along the sweep is both the more
#: honest model and the one that produces a curve rather than a step.
UNCERTAIN_CENTRES = (-0.16, -0.06, 0.05, 0.13, 0.18)  # [rad] on joint 0
UNCERTAIN_WIDTH = 0.022  # [rad]
UNCERTAIN_MAGNITUDE = 0.08  # [rad] peak perturbation

#: How close the body must be to a stored correction for it to be recalled [rad].
#: Narrow enough that one correction does not answer for a situation it was never given
#: in — which would make the intervention rate fall for the wrong reason.
RECALL_RADIUS = 0.02

#: Below this confidence, hand over. Matches the shipped skill.
THRESHOLD = 0.5

#: Trailing window for the intervention rate. Small enough to move within one run,
#: large enough that a single episode does not swing it.
WINDOW = 10


@dataclass
class Outcome:
    episode: int
    interrupted: bool
    corrected: bool
    steps: int
    corrections_known: int


def sweep(dof: int, *, phase: float = 0.0, amplitude: float = 0.2, period: int = 240):
    """A sweep that starts from a different place each episode.

    The phase matters more than it looks. With every episode running the identical
    trajectory, the first one meets every uncertain region, gets corrected at all of them,
    and no episode after it ever interrupts — a step function, not a learning curve, and it
    would overstate what happened.

    A real arm does not start from the same pose every time. Varying the phase means each
    episode encounters a different subset of the situations the policy is unsure about, so
    corrections accumulate the way they actually would.
    """
    import math

    def fn(step: int) -> list[float]:
        return [amplitude * math.sin(2 * math.pi * (step / period) + phase)] + [0.0] * (dof - 1)

    return fn


class ScriptedOperator:
    """An operator who knows what the arm should have done.

    Stands in for a human. When asked, it supplies the *unperturbed* trajectory — the
    action the policy would have proposed if it were certain. That is the honest analogue
    of a person who can see the situation clearly and shows the robot what to do.

    It corrects rather than approves, because approving teaches nothing: the loop being
    demonstrated is correction becoming behaviour, and an approval carries no information
    to become anything.
    """

    def __init__(self, fn, dof: int, control_hz: float, chunk_size: int) -> None:
        self._fn = fn
        self._dof = dof
        self._control_hz = control_hz
        self._chunk_size = chunk_size
        self.calls = 0

    def resolve(self, context):
        from tendon.kernel.types import (
            Action,
            ActionSpace,
            Confidence,
            ConfidenceSource,
            Intent,
            InterruptResolution,
            Resolution,
        )

        self.calls += 1
        step = context.step
        actions = tuple(
            Action(space=ActionSpace.JOINT_POSITION, values=self._fn(step + i))
            for i in range(self._chunk_size)
        )
        correction = Intent(
            horizon_s=self._chunk_size / self._control_hz,
            actions=actions,
            confidence=Confidence(
                score=1.0,
                source=ConfidenceSource.CHUNK_VARIANCE,
                reasons=("supplied by the operator",),
            ),
            goal="operator correction",
        )
        return InterruptResolution(
            resolution=Resolution.CORRECTED,
            correction=correction,
            note="approach through the uncertain region like this",
        )


# ------------------------------------------------------------------------------- running


def run_episodes(episodes: int, steps: int, seed: int) -> list[Outcome]:
    import math

    from tendon.kernel.scheduler import Scheduler
    from tendon.kernel.types import SafetyLimits
    from tendon.services.adaptive import (
        AdaptivePolicy,
        CorrectionMemory,
        StochasticPolicy,
        UncertainRegion,
    )
    from tendon.services.bodies import open_body

    body = open_body("mujoco")
    capability = body.capability

    memory = CorrectionMemory(radius=RECALL_RADIUS)
    outcomes: list[Outcome] = []

    try:
        for index in range(episodes):
            # A different starting phase each episode, deterministic in the seed so the
            # whole run is repeatable.
            phase = (index * 2 * math.pi * 0.37) % (2 * math.pi)
            fn = sweep(capability.dof, phase=phase)
            operator = ScriptedOperator(fn, capability.dof, capability.control_hz, chunk_size=10)
            inner = StochasticPolicy(
                fn,
                control_hz=capability.control_hz,
                dof=capability.dof,
                regions=tuple(
                    UncertainRegion(
                        joint=0,
                        centre=centre,
                        width=UNCERTAIN_WIDTH,
                        magnitude=UNCERTAIN_MAGNITUDE,
                    )
                    for centre in UNCERTAIN_CENTRES
                ),
                reference_spread=0.004,
                seed=seed + index,
            )
            # One memory across every episode. That is the whole point: what the operator
            # taught in episode 3 has to still be there in episode 30.
            policy = AdaptivePolicy(inner, memory=memory)

            scheduler = Scheduler(
                driver=body,
                limits=SafetyLimits(max_joint_velocity=4.0),
                confidence_threshold=THRESHOLD,
                handler=operator,
                on_intervention=policy.learn_from,
            )
            result = scheduler.run_episode(policy, max_steps=steps, seed=seed + index)

            outcomes.append(
                Outcome(
                    episode=index,
                    interrupted=result.interventions > 0,
                    corrected=result.corrections > 0,
                    steps=result.steps,
                    corrections_known=len(memory),
                )
            )
    finally:
        body.close()

    return outcomes


# ------------------------------------------------------------------------------ reporting


def curve(outcomes: list[Outcome], window: int) -> list[tuple[int, float]]:
    """(cumulative corrections, intervention rate over a trailing window).

    Trailing rather than cumulative: a cumulative rate is dominated by early episodes and
    keeps falling after improvement stops, which would make this graph look right for the
    wrong reason. Same argument as `services/evaluator.intervention_curve`.
    """
    points: list[tuple[int, float]] = []
    for i in range(window - 1, len(outcomes)):
        recent = outcomes[i + 1 - window : i + 1]
        rate = sum(1 for o in recent if o.interrupted) / len(recent)
        points.append((outcomes[i].corrections_known, rate))
    return points


def _glyphs() -> tuple[str, str, str, str]:
    """Block-drawing characters, or ASCII where the console cannot encode them.

    The result of this example is one graph, and it is the graph the whole project is
    judged on. Crashing while drawing it is worse than drawing it plainly.

    A Windows console in a Korean locale runs cp949 and raises `UnicodeEncodeError` on
    `U+2588 FULL BLOCK`, so the run reaches sixty episodes, computes the curve, writes the
    CSV, and then dies on `print`. Probing the actual stream is better than guessing from
    the platform: the same script redirected to a UTF-8 file should still get the nice
    characters.
    """
    fill, bar, corner, rule = "█", "│", "└", "─"
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        (fill + bar + corner + rule).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return "#", "|", "+", "-"
    return fill, bar, corner, rule


def sparkline(points: list[tuple[int, float]], height: int = 8, width: int = 56) -> str:
    """A plot that needs no dependency, so the result is visible from a terminal."""
    if not points:
        return "(not enough episodes for a full window)"

    fill, bar, corner, rule = _glyphs()
    rates = [rate for _, rate in points]
    step = max(1, len(rates) // width)
    sampled = rates[::step][:width]

    rows = []
    for level in range(height, 0, -1):
        threshold = level / height
        row = "".join(fill if r >= threshold - 0.5 / height else " " for r in sampled)
        label = f"{threshold:>4.0%} "
        rows.append(label + bar + row)
    rows.append("     " + corner + rule * len(sampled))
    rows.append(f"      0{' ' * (len(sampled) - 8)}{points[-1][0]:>3} corrections")
    return "\n".join(rows)


def write_csv(points: list[tuple[int, float]], outcomes: list[Outcome], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)

    with (out / "intervention_curve.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cumulative_corrections", "intervention_rate"])
        writer.writerows(points)

    with (out / "episodes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["episode", "interrupted", "corrected", "steps", "corrections_known"])
        for o in outcomes:
            writer.writerow(
                [o.episode, int(o.interrupted), int(o.corrected), o.steps, o.corrections_known]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=int, default=60)
    parser.add_argument("--steps", type=int, default=240)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--window", type=int, default=WINDOW)
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args()

    try:
        import mujoco  # noqa: F401
    except ImportError:
        print('this example needs the simulator:  pip install -e ".[sim]"')
        return 1

    print(f"running {args.episodes} episodes of up to {args.steps} steps")
    outcomes = run_episodes(args.episodes, args.steps, args.seed)

    first = outcomes[: args.window]
    last = outcomes[-args.window :]
    before = sum(1 for o in first if o.interrupted) / len(first)
    after = sum(1 for o in last if o.interrupted) / len(last)

    points = curve(outcomes, args.window)
    write_csv(points, outcomes, args.out)

    print()
    print("intervention rate over a trailing window of", args.window)
    print(sparkline(points))
    print()
    print(f"  first {len(first)} episodes : {before:.0%} interrupted")
    print(f"  last  {len(last)} episodes : {after:.0%} interrupted")
    print(f"  corrections stored    : {outcomes[-1].corrections_known}")
    print(f"  written to            : {args.out}/intervention_curve.csv")

    if outcomes[-1].corrections_known == 0:
        print()
        print("FAIL - no corrections were ever recorded, so nothing could have been learned.")
        print("The policy never dropped below the confidence threshold.")
        return 1

    if after >= before:
        print()
        print("The intervention rate did not fall.")
        print("That is a real result and belongs in the README as prominently as a fall.")
        return 1

    print()
    print("PASS - the loop closes: corrections reduced how often the policy asked for help.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
