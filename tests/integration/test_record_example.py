"""The v0.1 acceptance example, held to what it claims.

`examples/01_record` exists to answer one question by measurement: does an episode land in
the store without anybody switching recording on? For most of this project's life it did
not answer that. `_run` took a `record` flag and never read it, so both arms of
`--overhead` executed identical code, the comparison was a run against itself, and the
script printed that v0.1 acceptance was met after measuring nothing. It also announced
"episodes are written to the store" while attaching no recorder at all — `tendon episodes`
reported an empty store immediately afterwards.

The tests that matter here are therefore the negative ones. That the recording arm writes
something is easy to get right by accident; that the non-recording arm writes *nothing*
is what fails when the two paths quietly become the same code again.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "01_record" / "run.py"

#: Enough steps for the bus to have a mean worth reading, few enough that encoding the
#: episode twice does not dominate the suite.
STEPS = 120


def load_example():
    spec = importlib.util.spec_from_file_location("record_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["record_example"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def example():
    return load_example()


@pytest.fixture(scope="module")
def recorded(example, tmp_path_factory):
    """One episode with the recorder subscribed to the scheduler's bus."""
    root = tmp_path_factory.mktemp("store-recording")
    return example._episode(steps=STEPS, driver_name="mujoco", recording=True, root=root)


@pytest.fixture(scope="module")
def bare(example, tmp_path_factory):
    """The same episode with nothing subscribed. Its own store, so the counts are
    independent rather than one arm reading the other's leftovers."""
    root = tmp_path_factory.mktemp("store-bare")
    return example._episode(steps=STEPS, driver_name="mujoco", recording=False, root=root)


# ------------------------------------------------------------------ it records


def test_the_episode_runs(recorded) -> None:
    assert recorded.steps > 0


def test_an_episode_lands_in_the_store(recorded) -> None:
    """The claim. Counted through `services.store`, which reads the disk layout and cannot
    import the recorder — so this is an independent reading rather than the recorder being
    asked to confirm its own work."""
    assert recorded.store_after > recorded.store_before


def test_nothing_had_to_be_switched_on(example) -> None:
    """Decision 1 is structural because the control loop has no branch for it. If a flag
    ever appears in the scheduler, recording becomes something that can be off."""
    source = (REPO / "src/tendon/kernel/scheduler.py").read_text(encoding="utf-8")
    assert "record=" not in source
    assert "if record" not in source


# -------------------------------------------------- and the other arm does not


def test_not_recording_writes_nothing(bare) -> None:
    """The test that would have caught the bug.

    The previous version ignored its own `record` argument, so this arm wrote episodes
    too — and the overhead comparison was a run measured against itself.
    """
    assert bare.store_after == bare.store_before == 0


def test_the_recorder_is_only_on_the_hot_path_when_recording(recorded, bare) -> None:
    """Checked at the bus rather than on disk, because that is where the cost is paid.
    Identical publish costs would mean the two arms are the same code whatever the store
    ends up containing."""
    assert recorded.publish_ms > 0.0
    assert bare.publish_ms == 0.0


# ------------------------------------------------------------------- and cheaply


def test_recording_fits_inside_the_control_period(example, recorded) -> None:
    """The verdict the example prints.

    A budget share rather than a wall-clock difference between two runs: that difference
    is mostly scheduling noise on a machine doing anything else, and it is what the old
    script used to justify a PASS.
    """
    assert recorded.budget_pct < example.BUDGET_LIMIT_PCT, (
        f"recording took {recorded.publish_ms:.3f}ms of a {recorded.period_ms:.1f}ms period "
        f"({recorded.budget_pct:.2f}%). Above {example.BUDGET_LIMIT_PCT}% it gets switched off."
    )


def test_the_verdict_is_not_a_constant(example) -> None:
    """`budget_pct` is derived, not stored. A version of this that returned a fixed number
    would pass every other test in this file."""
    cheap = example.Run(
        steps=10, per_step_ms=1.0, publish_ms=0.1, period_ms=10.0, store_before=0, store_after=1
    )
    dear = example.Run(
        steps=10, per_step_ms=1.0, publish_ms=5.0, period_ms=10.0, store_before=0, store_after=1
    )

    assert cheap.budget_pct == pytest.approx(1.0)
    assert dear.budget_pct == pytest.approx(50.0)
