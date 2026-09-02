"""`tendon calibrate` measures a scale, and everything around it says which is which.

The command produces one number and the number is easy to misread. A reference spread is
not a threshold: it says what disagreement is *typical* for this policy on this body, and
says nothing about how much of it means somebody should take over. ADR 0003 puts the second
one in v0.3 because it needs episodes where a human did.

So most of what is checked here is what the command refuses and what it says, not what it
computes — a measurement presented as more than it is would put a confident number in front
of an operator, and the interrupt path reads that number.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tendon.cli.main import app

RUNNER = CliRunner()
REPO = Path(__file__).resolve().parents[2]


def test_calibrate_refuses_a_policy_name_before_opening_a_body(tmp_path: Path) -> None:
    """Same rule as `run` and `eval`: with `--physical` a body is a real arm, and opening
    one to say a name was misspelled is the ordering this project has fixed twice."""
    result = RUNNER.invoke(
        app,
        ["calibrate", "skills/grasp/cube-sim", "--policy", "scriptd", "--driver", "absent"],
    )

    assert result.exit_code == 1
    assert "scriptd" in result.output
    assert "unknown driver" not in result.output


def test_calibrate_refuses_an_adapter_path_before_opening_a_body(tmp_path: Path) -> None:
    result = RUNNER.invoke(
        app,
        [
            "calibrate",
            "skills/grasp/cube-sim",
            "--adapter",
            str(tmp_path / "nothing-here"),
            "--driver",
            "absent",
        ],
    )

    assert result.exit_code == 1
    assert "adapter" in result.output
    assert "unknown driver" not in result.output


def test_the_help_separates_the_scale_from_the_threshold() -> None:
    """The one thing a reader has to take away. A reference spread read as a threshold
    would be set as one, and `interrupt.confidence_threshold` is what decides handover."""
    result = RUNNER.invoke(app, ["calibrate", "--help"])

    assert result.exit_code == 0
    assert "threshold" in result.output
    assert "typical" in result.output


def test_calibrate_writes_no_episodes() -> None:
    """It drives the body to produce observations, not to collect data. Four hundred steps
    landing in the store would put a run nobody asked for in front of the curator, and
    `tendon curate` ranks what it finds."""
    source = (REPO / "src" / "tendon" / "cli" / "main.py").read_text(encoding="utf-8")
    body = source.partition("def calibrate(")[2].partition("\n@app.command")[0]

    assert "_attach_recorder" not in body, "calibration is recording episodes"
    assert "_record_progress" not in body, "calibration is writing points on the v0.3 graph"


def test_the_report_says_what_the_skill_s_threshold_would_do() -> None:
    """The one number neither the scale nor the threshold gives on its own.

    Printed through a helper rather than checked by running `calibrate`, which needs a
    thousand control steps and a checkpoint. The arithmetic is tested in
    `test_calibration.py`; this is that it reaches a person.
    """
    from rich.console import Console

    from tendon.cli.main import _report_thresholds
    from tendon.services.calibration import from_spreads

    measured = from_spreads(
        [0.001 + 0.0001 * index for index in range(100)],
        skill="grasp/cube-sim",
        body="mujoco:arm",
        policy="test",
        measured_at="2026-08-31T00:00:00Z",
    )

    console = Console(width=200, record=True)
    _report_thresholds(console, measured, 0.5)
    printed = console.export_text()

    assert "0.5" in printed
    assert "50%" in printed, "the half-of-everything consequence is the finding"
    assert "skill.yaml" in printed, "the declared threshold is not marked in the table"
    assert "ADR 0003" in printed, "it should not read as though the threshold were settled"


def test_a_declared_threshold_outside_the_table_is_still_reported() -> None:
    """A skill can declare anything. The table is a fixed set of comparisons and the
    skill's own value has to be answered whether or not it is one of them."""
    from rich.console import Console

    from tendon.cli.main import _report_thresholds
    from tendon.services.calibration import from_spreads

    measured = from_spreads(
        [0.001] * 50,
        skill="grasp/cube-sim",
        body="mujoco:arm",
        policy="test",
        measured_at="2026-08-31T00:00:00Z",
    )

    console = Console(width=200, record=True)
    _report_thresholds(console, measured, 0.37)

    assert "0.37" in console.export_text()


def test_the_measured_scale_is_used_by_the_run_that_follows() -> None:
    """A measurement nothing reads is the shape this repository keeps finding. The adapter
    path loads it, checks it was measured from the same policy, and falls back to reporting
    no scale rather than to a number from somewhere else."""
    source = (REPO / "src" / "tendon" / "cli" / "main.py").read_text(encoding="utf-8")
    adapter_body = source.partition("def _adapter_policy(")[2].partition("\ndef ")[0]

    assert "load_calibration" in adapter_body
    assert "measured.policy" in adapter_body, "a scale from another policy would be used"
    assert "reference_spread=spread" in adapter_body
