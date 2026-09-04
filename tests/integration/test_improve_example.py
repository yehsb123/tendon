"""The claim at the top of the README, held in place.

`examples/04_improve` produces the graph the project leads with: an intervention rate
falling as corrections accumulate. Nothing was checking that it still does. The scheduler,
the policy, the safety path and the interrupt machine have all changed since that figure
was measured, and any of those changes could have quietly flattened the line while every
other test stayed green.

## What is asserted, and what is not

**Asserted:** the rate falls, corrections are stored, and the run reports success. Those
are properties of the loop.

**Not asserted:** the specific numbers in the README. Pinning 100% and 20% would make this
a test of the random seed and the sweep parameters, and it would fail for reasons that mean
nothing — a changed epsilon, a different chunk size. The README says those figures came
from a run, not that they are guaranteed.

Runs at a reduced scale. The full example is 60 episodes of 240 steps; this is enough to
show the shape without putting a minute into every CI run.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "04_improve" / "run.py"


def load_example():
    """Import the example as a module.

    Loaded rather than shelled out to, so a failure points at a line instead of an exit
    code — and so this tests the same functions the example runs rather than a copy of
    them.
    """
    spec = importlib.util.spec_from_file_location("improve_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["improve_example"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def outcomes():
    """One reduced run, shared across the assertions below.

    Module-scoped because it drives MuJoCo for a few hundred steps, and running it once
    per assertion would make this the slowest file in the suite for no added coverage.
    """
    example = load_example()
    return example, example.run_episodes(episodes=24, steps=120, seed=0)


def test_the_example_still_runs(outcomes) -> None:
    _, results = outcomes
    assert len(results) == 24
    assert all(o.steps > 0 for o in results)


def test_every_episode_is_judged(outcomes) -> None:
    """The half of the claim this example could not make.

    A falling intervention rate and a policy that stopped *trying* are the same line.
    Until `drivers/mujoco.py` reported the cube's height nothing could tell them apart, so
    the example printed PASS on the fall alone and every episode was unjudged.
    """
    _, results = outcomes

    assert all(outcome.succeeded is not None for outcome in results), (
        "an episode came back unjudged; the body is not reporting what the skill's "
        "success criteria name, and the graph is ambiguous again"
    )


def test_this_policy_never_succeeds_and_the_example_says_so(outcomes) -> None:
    """The measured answer, pinned because it is the point.

    The operator here corrects a joint sweep. It does not reach for the cube and was never
    going to, so success is 0% throughout while the intervention rate falls to a tenth.
    That is the exact shape somebody would misread as learning, and it is now printed
    beside the PASS rather than left to be assumed away.

    If this ever starts succeeding, the example has changed into something else and its
    verdict text needs rewriting — which is what the failure of this test means.
    """
    example, results = outcomes

    assert not any(outcome.succeeded for outcome in results)

    verdict = inspect.getsource(example.verdict) if hasattr(example, "verdict") else ""
    source = EXAMPLE.read_text(encoding="utf-8")
    assert "never succeeded" in (verdict or source), (
        "the example no longer states that the task was not achieved"
    )
    assert "the loop runs" in source and "the loop learns" in source


def test_corrections_are_stored(outcomes) -> None:
    """Without this the loop is open and the graph means nothing — the policy would be
    asked and taught nothing, which is exactly the bug the shell's Correct button had."""
    _, results = outcomes
    assert results[-1].corrections_known > 0


def test_the_policy_asks_for_help_at_first(outcomes) -> None:
    """A run that never interrupts would produce a flat line at zero and look like
    success. The falling rate only means something if it started high."""
    _, results = outcomes
    assert any(o.interrupted for o in results[:8])


def test_the_intervention_rate_falls(outcomes) -> None:
    """The claim itself.

    Compared as first window against last rather than by fitting a line: the question is
    whether teaching it changed how often it asks, and a window comparison answers that
    without inventing a model of the curve.
    """
    _, results = outcomes
    window = 8

    first = results[:window]
    last = results[-window:]
    before = sum(1 for o in first if o.interrupted) / len(first)
    after = sum(1 for o in last if o.interrupted) / len(last)

    assert after < before, (
        f"interventions did not fall: {before:.0%} over the first {window} episodes, "
        f"{after:.0%} over the last {window}. The loop is open."
    )


def test_the_curve_is_a_trailing_window_not_a_cumulative_rate(outcomes) -> None:
    """A cumulative rate is dominated by early episodes and keeps falling after
    improvement stops, which would make this graph look right for the wrong reason."""
    example, results = outcomes
    points = example.curve(results, window=8)

    assert points, "not enough episodes for a full window"
    # Ten clean episodes after ten interrupted ones must reach zero on a trailing window;
    # a cumulative rate never would.
    assert min(rate for _, rate in points) < max(rate for _, rate in points)


def test_corrections_only_ever_accumulate(outcomes) -> None:
    """The x-axis of the graph. A count that went down would mean something was forgotten,
    and nothing in the loop forgets."""
    _, results = outcomes
    counts = [o.corrections_known for o in results]
    assert counts == sorted(counts)
