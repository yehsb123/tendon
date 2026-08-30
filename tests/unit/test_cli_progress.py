"""The graph, for somebody who is not sitting in front of a browser.

Watching a rig usually means an ssh session. `tendon episodes`, `tendon curate` and
`tendon eval` all have a terminal form; the line the project is measured by did not, so the
one thing worth watching was the one thing only visible in the shell.

## Why ASCII and not block characters

`tests/unit/test_console_output.py` exists because this project has crashed four times
printing characters a cp949 console cannot encode — including once from the script whose
purpose was to report failures. A progress chart that raised `UnicodeEncodeError` while
reporting progress would be a fitting way to lose that argument, so the chart is `#` and
`-`. The README draws the same shape with block characters, which is fine: markdown is not
a terminal.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tendon.cli.main import _CHART_WIDTH, _chart, app
from tendon.services.progress import EpisodeRecord, append

RUNNER = CliRunner()


def record(index: int, *, interrupted: bool, corrections: int) -> EpisodeRecord:
    return EpisodeRecord(
        skill="grasp/cube-sim",
        body="mujoco:so_arm100_cube",
        episode_id=str(index),
        ended_at="2026-01-01T00:00:00+00:00",
        steps=200,
        interventions=1 if interrupted else 0,
        corrections=1 if interrupted else 0,
        corrections_known=corrections,
    )


def history_of(root: Path, count: int, *, falls: bool = True) -> None:
    for index in range(count):
        interrupted = index < count // 2 if falls else True
        append(
            root,
            "grasp/cube-sim",
            "mujoco:so_arm100_cube",
            record(index, interrupted=interrupted, corrections=min(index + 1, 12)),
        )


def run(root: Path, *args: str):
    return RUNNER.invoke(app, ["progress", "--store", str(root), *args])


# ------------------------------------------------------------------- the command


def test_it_draws_the_line(tmp_path: Path) -> None:
    history_of(tmp_path, 30)
    result = run(tmp_path)

    assert result.exit_code == 0, result.output
    assert "#" in result.output
    assert "corrections" in result.output


def test_it_says_when_there_is_not_enough_yet(tmp_path: Path) -> None:
    """Rather than drawing a rate over four episodes, which is not a rate."""
    history_of(tmp_path, 4)
    result = run(tmp_path)

    assert result.exit_code == 0, result.output
    assert "not enough yet" in result.output
    assert "#" not in result.output


def test_nothing_run_exits_non_zero_with_somewhere_to_go(tmp_path: Path) -> None:
    result = run(tmp_path / "empty")

    assert result.exit_code == 1
    assert "tendon serve" in result.output


def test_the_window_can_be_changed(tmp_path: Path) -> None:
    """Five episodes is not enough for the default window and is enough for a window of
    four, so this fails if the option is accepted and ignored."""
    history_of(tmp_path, 5)

    assert "not enough yet" in run(tmp_path).output
    assert "#" in run(tmp_path, "--window", "4").output


# -------------------------------------------------------------------- the chart


def test_the_chart_is_ascii() -> None:
    """The rule that has been broken four times, applied where it is easiest to break.

    Checked on the drawing function rather than only on the command's output, because a
    chart is built character by character and that is exactly where a block character gets
    typed in.
    """
    lines = _chart(tuple((i, 1.0 - i / 20) for i in range(20)))

    assert lines
    for line in lines:
        line.encode("cp949")


def test_a_long_history_is_sampled_rather_than_cut(tmp_path: Path) -> None:
    """The interesting part of this line is usually the end.

    Showing the first fifty points of a thousand-episode history would hide precisely the
    part somebody opened it to see.
    """
    points = tuple((i, 1.0 - i / 500) for i in range(500))
    lines = _chart(points)

    assert lines
    # The axis label carries the last point's x, so the end survived the sampling.
    assert str(points[-1][0]) in lines[-1]


def test_the_chart_fits_in_a_terminal() -> None:
    """Eighty columns, because a chart that wraps is a chart that cannot be read.

    Written first as `_CHART_WIDTH + 12`, which was wrong — the axis label is indented and
    carries the word "corrections" — and a bound derived from the wrong geometry is a test
    that fails on correct code.
    """
    lines = _chart(tuple((i, 0.5) for i in range(500)))

    assert lines
    assert _CHART_WIDTH < 80
    for line in lines:
        assert len(line) <= 80, line


def test_an_empty_curve_draws_nothing(tmp_path: Path) -> None:
    """Not an empty axis. There is a difference between a rate of zero and no rate, and an
    axis with nothing on it reads like the first."""
    assert _chart(()) == []
