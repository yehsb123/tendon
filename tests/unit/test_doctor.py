"""`tendon doctor` — the first command anyone runs.

The regression test that matters here is the markup one. Rich reads square brackets as
style tags, and remedies contain them, so `pip install -e ".[view]"` rendered as
`pip install -e "."`. Telling someone the wrong command is worse than telling them nothing,
and nothing about the output looked broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tendon.cli.doctor import Check, Status, run_checks, summarise
from tendon.cli.main import app

runner = CliRunner()


# ------------------------------------------------------------------------------- checks


def test_every_check_reports_a_status_and_a_detail() -> None:
    checks = run_checks()
    assert checks
    for check in checks:
        assert check.name
        assert check.detail, f"{check.name} has no detail"
        assert isinstance(check.status, Status)


def test_checks_are_ordered_by_what_blocks_what() -> None:
    """An interpreter that cannot run the code makes everything else moot."""
    names = [c.name for c in run_checks()]
    assert names[0] == "python"
    assert names.index("simulation") < names.index("hub")


def test_a_missing_optional_piece_is_limited_not_blocked() -> None:
    """The Hub is not needed before v0.4, so its absence must never block."""
    hub = next(c for c in run_checks() if c.name == "hub")
    assert hub.status is not Status.BLOCKED


def test_anything_not_ok_offers_a_remedy() -> None:
    """A checklist that says something is wrong without saying what to do is a puzzle."""
    for check in run_checks():
        if check.status is not Status.OK:
            assert check.remedy, f"{check.name} is {check.status.value} with no remedy"


# ---------------------------------------------------------------------------- summarise


def test_summary_of_a_clean_environment() -> None:
    checks = [Check("a", Status.OK, "fine"), Check("b", Status.OK, "fine")]
    status, message = summarise(checks)
    assert status is Status.OK
    assert "Everything" in message


def test_summary_names_what_is_limited() -> None:
    checks = [Check("a", Status.OK, "fine"), Check("training", Status.LIMITED, "no gpu")]
    status, message = summarise(checks)
    assert status is Status.LIMITED
    assert "training" in message
    assert "record" in message, "a limited environment should say what still works"


def test_summary_of_a_blocked_environment_says_nothing_can_run() -> None:
    checks = [Check("simulation", Status.BLOCKED, "no mujoco", "install it")]
    status, message = summarise(checks)
    assert status is Status.BLOCKED
    assert "simulation" in message
    assert "Nothing can run" in message


def test_blocking_beats_limited_in_the_summary() -> None:
    checks = [
        Check("training", Status.LIMITED, "no gpu"),
        Check("simulation", Status.BLOCKED, "no mujoco", "install it"),
    ]
    status, _ = summarise(checks)
    assert status is Status.BLOCKED


# --------------------------------------------------------------------------------- cli


def test_doctor_runs_and_prints_every_check() -> None:
    result = runner.invoke(app, ["doctor"])
    for check in run_checks():
        assert check.name in result.output


def test_remedies_survive_rich_markup() -> None:
    """The regression. Square brackets in a remedy must reach the terminal intact.

    Before escaping, `pip install -e ".[view]"` printed as `pip install -e "."` — a command
    that runs, installs the wrong thing, and gives no sign anything was lost.
    """
    result = runner.invoke(app, ["doctor"])
    bracketed = [c for c in run_checks() if "[" in c.remedy]

    if not bracketed:
        pytest.skip("no bracketed remedy applies in this environment")

    for check in bracketed:
        assert check.remedy in result.output, (
            f"remedy for {check.name} was mangled: expected {check.remedy!r}"
        )


def test_details_survive_rich_markup_too() -> None:
    """Details are shown through the same path and can contain brackets."""
    result = runner.invoke(app, ["doctor"])
    for check in run_checks():
        if "[" in check.detail:
            assert check.detail in result.output


def test_doctor_touches_no_hardware(tmp_path: Path) -> None:
    """Must be safe to run on a machine with a robot attached.

    Checked structurally: nothing in the module calls a driver method that moves anything.
    A driver is only asked which bodies are registered.
    """
    source = (Path(__file__).resolve().parents[2] / "src/tendon/cli/doctor.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (".apply(", ".reset(", "load("):
        assert forbidden not in source, f"doctor must not call {forbidden}"


def test_version_command_prints_a_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip()
