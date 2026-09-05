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


def test_a_limited_thing_is_not_described_as_unavailable() -> None:
    """`Status.LIMITED` is defined as *works, but something is degraded*. The summary
    listed every limited check under "Not yet available", which on a real machine read
    "Not yet available: storage, training" with 11 GB free and `tendon train` having
    produced an adapter half an hour earlier.

    The first command anybody runs was telling them the thing they had just done could not
    be done.
    """
    checks = [Check("training", Status.LIMITED, "cpu-only torch")]
    _, message = summarise(checks)

    assert "not yet available" not in message.lower()
    assert "unavailable" not in message.lower()
    assert "limit" in message.lower(), "it should say what LIMITED actually means"


def test_the_summary_does_not_guess_which_capabilities_survive() -> None:
    """It used to claim "you can run and record episodes" whenever anything was limited.
    That is a guess about *which* check is limited, and a degraded `datasets` is precisely
    the one that stops recording — so the sentence was most likely to be wrong in the case
    it was written for.

    Each check's own line already says what its limitation costs, which is where a claim
    about a specific capability belongs.
    """
    checks = [Check("datasets", Status.LIMITED, "lerobot missing, so nothing is recorded")]
    _, message = summarise(checks)

    assert "record" not in message.lower()


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


def test_remedies_survive_rich_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression. Square brackets in a remedy must reach the terminal intact.

    Before escaping, `pip install -e ".[view]"` printed as `pip install -e "."` — a command
    that runs, installs the wrong thing, and gives no sign anything was lost.

    The checks are stubbed rather than read from the environment. An earlier version of
    this test skipped itself on a machine where every bracketed remedy happened to be
    satisfied, which meant the regression test stopped running exactly when someone had a
    working setup — and a test that only runs on broken machines guards nothing.
    """
    bracketed = Check(
        "visualisation",
        Status.LIMITED,
        "missing — install with the [view] extra",
        'pip install -e ".[view]"',
    )
    monkeypatch.setattr("tendon.cli.main.run_checks", lambda: [bracketed])

    result = runner.invoke(app, ["doctor"])

    assert 'pip install -e ".[view]"' in result.output, f"remedy was mangled: {result.output!r}"
    assert "[view] extra" in result.output, "a bracketed detail was mangled"


def test_a_cpu_only_torch_is_not_reported_as_a_missing_gpu() -> None:
    """Different problems must not be reported the same way.

    A CPU-only wheel on a machine that has a GPU is a reinstall; no GPU at all is a
    hardware purchase. Conflating them sends someone shopping for what they already own.
    """
    training = next(c for c in run_checks() if c.name == "training")
    if "CPU-only build" in training.detail:
        assert "index-url" in training.remedy, (
            "a CPU-only build must be remedied by reinstalling torch, not by buying a GPU"
        )


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


# --------------------------------------------------------- commands that are not ready


def test_an_unavailable_command_explains_rather_than_tracebacks() -> None:
    """A NotImplementedError reaching a user is the tool telling them its own source is
    incomplete, in a format meant for whoever wrote it.

    They asked what the command does. The useful answer is when it will do it and what is
    already there.

    Written against `curate`, then moved to `train`, and now against neither: `train` was
    the last stub and it runs. No command calls `_not_yet` any more.

    So this tests the helper directly. Deleting it with the last stub would mean the next
    person to add one writes `raise NotImplementedError`, which is exactly the shape this
    was written to prevent — and a rule with no instance left is the easiest kind to lose.
    Pairs with `test_every_command_either_runs_or_says_why_not`, which is what notices a
    stub arriving without it.
    """
    import typer

    from tendon.cli.main import _not_yet

    stub = typer.Typer()

    @stub.command()
    def later() -> None:
        _not_yet("later", "v0.9", "the thing it waits on, and what already exists")

    result = runner.invoke(stub, [])

    assert result.exit_code == 1
    assert "not available yet" in result.output
    assert "v0.9" in result.output
    assert "the thing it waits on" in result.output
    assert "Traceback" not in result.output
    assert "NotImplementedError" not in result.output


def test_no_command_is_a_stub_any_more() -> None:
    """The state the test above is describing, asserted rather than left in a docstring to
    go stale — that is how the previous two versions of it ended up naming commands that
    had started working."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src" / "tendon" / "cli" / "main.py"
    body = source.read_text(encoding="utf-8")

    assert body.count("_not_yet(") == 1, (
        "a command calls _not_yet again; point the test above at it, since a real stub is "
        "a better guard than a synthetic one"
    )


def test_every_command_either_runs_or_says_why_not() -> None:
    """The failure this repository kept producing: a command offered and not performed.

    Checked structurally, because the next stub to be added will be added by someone who
    has forgotten this rule.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src/tendon/cli/main.py").read_text(
        encoding="utf-8"
    )
    assert "raise NotImplementedError" not in source, (
        "a CLI command raises NotImplementedError; use _not_yet() so the user is told "
        "when it will work and what already exists"
    )
