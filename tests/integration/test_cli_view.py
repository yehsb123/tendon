"""`tendon run --view` — the logger that had 27 tests and no caller.

`services/viz.py` streams a run into Rerun: commanded against applied on the same axes,
confidence against the threshold that would raise an interrupt, and where safety clamped.
It is written to attach to the scheduler's bus exactly the way the recorder does. Nothing
in the command line or the API referenced it.

## Why this one has a flag and recording does not

The recorder costs 0.04 ms per step and is always attached because of it — design decision
1 is structural only because nobody would ever want it off. This costs enough that
`viz.py`'s own docstring says to attach it to a run being watched and not to every run
being collected. A flag on the wrong one of these two is the difference between a project
that collects data and a project that intends to.

So the tests here check both halves: that asking produces a recording, and that not asking
leaves the hot path alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")
pytest.importorskip("rerun", reason="needs the view extra: pip install -e '.[view]'")

from tendon.cli.main import app  # noqa: E402

RUNNER = CliRunner()
STEPS = 40


def run(*args: str):
    return RUNNER.invoke(app, ["run", "grasp/cube-sim", "--steps", str(STEPS), *args])


def test_a_recording_is_written_when_asked_for(tmp_path: Path) -> None:
    out = tmp_path / "run.rrd"
    result = run("--store", str(tmp_path / "store"), "--view-save", str(out))

    assert result.exit_code == 0, result.output
    assert out.is_file(), "asked for a recording and did not get one"
    assert out.stat().st_size > 0, "the recording was never flushed"


def test_it_says_where_it_went(tmp_path: Path) -> None:
    out = tmp_path / "run.rrd"
    result = run("--store", str(tmp_path / "store"), "--view-save", str(out))

    assert str(out) in result.output or "Rerun recording" in result.output


def test_nothing_is_attached_when_nobody_asked(tmp_path: Path) -> None:
    """The half that matters more.

    An episode nobody is watching must not pay for a viewer. If this ever starts attaching
    by default, the cost lands on every collected run and the line in `viz.py` about what
    to attach where stops being true.
    """
    result = run("--store", str(tmp_path / "store"))

    assert result.exit_code == 0, result.output
    assert "rerun" not in result.output.lower()


def test_the_cost_line_names_the_expensive_subscriber(tmp_path: Path) -> None:
    """With a viewer attached there are two subscribers and the viewer is the dear one.

    The line used to say "recording cost", which would put the recorder's name on the
    viewer's cost — the reading that gets design decision 1 blamed for something it does
    not do.
    """
    result = run("--store", str(tmp_path / "store"), "--view-save", str(tmp_path / "r.rrd"))

    assert "subscribers cost" in result.output
    assert "slowest: rerun" in result.output


def test_the_run_is_still_recorded_while_being_watched(tmp_path: Path) -> None:
    """Watching is an addition, not a substitution. Two subscribers on one bus, which is
    what the bus is for."""
    from tendon.services.store import list_datasets

    store = tmp_path / "store"
    result = run("--store", str(store), "--view-save", str(tmp_path / "r.rrd"))

    assert result.exit_code == 0, result.output
    datasets = list_datasets(store)
    assert len(datasets) == 1
    assert datasets[0].episodes == 1
