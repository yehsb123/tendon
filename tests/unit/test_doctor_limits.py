"""`doctor` says whether this machine caps what a skill may ask for.

A broken ceiling now stops every run — `tendon run`, `tendon eval` and the API all refuse
rather than fall back to a skill's own numbers, because a site that wrote one believes it
has a bound. That refusal is correct and it arrives at the worst moment: somebody starts a
run and it will not go, for a reason nobody has connected to a file they edited last week.

`doctor` is the command whose entire job is to say what works here before anything is
attempted. This is the check that makes the refusal findable.

## Why an absent file is `ok` and not silence

It is the default rather than a fault, and "skills run under their own limits" is a sentence
somebody should be able to read deliberately. A check that only appeared when something was
configured would leave the ordinary case indistinguishable from the check not existing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tendon.cli.doctor import Status, run_checks


@pytest.fixture
def ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the limits file wherever a test wants it."""
    import tendon.services.limits as limits_module

    path = tmp_path / "limits.yaml"
    monkeypatch.setattr(limits_module, "DEFAULT_LIMITS_PATH", path)
    return path


def limits_check(checks):
    return next(c for c in checks if c.name == "limits")


def test_no_file_is_ok_and_says_so(ceiling: Path) -> None:
    check = limits_check(run_checks())

    assert check.status is Status.OK
    assert "own limits" in check.detail


def test_a_configured_ceiling_is_reported(ceiling: Path) -> None:
    """Named, not just acknowledged. An operator should be able to read the number here
    rather than open the file to find out what they capped."""
    ceiling.write_text("safety:\n  max_joint_velocity: 0.5\n", encoding="utf-8")

    check = limits_check(run_checks())

    assert check.status is Status.OK
    assert "0.5" in check.detail


def test_a_broken_ceiling_blocks(ceiling: Path) -> None:
    """`BLOCKED`, because it is: every run refuses while the file is unreadable.

    A `LIMITED` here would be a diagnostic saying "partly fine" about a machine on which
    nothing will start.
    """
    ceiling.write_text("safety: [not, a, mapping]\n", encoding="utf-8")

    check = limits_check(run_checks())

    assert check.status is Status.BLOCKED
    assert check.remedy


def test_the_remedy_names_the_file(ceiling: Path) -> None:
    """The whole value of finding this in `doctor` is being told which file. A remedy that
    said "fix your configuration" would leave the reader exactly where they started."""
    ceiling.write_text("safety: [broken]\n", encoding="utf-8")

    assert str(ceiling) in limits_check(run_checks()).remedy


def test_it_is_listed_before_the_optional_extras(ceiling: Path) -> None:
    """Order is the argument `run_checks` makes: what blocks what.

    A ceiling can stop a run; a missing visualiser extra cannot. Reading the list top to
    bottom should follow that.
    """
    names = [c.name for c in run_checks()]

    assert names.index("limits") < names.index("datasets")
    assert names.index("limits") < names.index("hub")


def test_a_blocked_ceiling_makes_the_whole_report_blocked(ceiling: Path) -> None:
    """`doctor` exits non-zero on `BLOCKED` so it can gate a script. A ceiling that stops
    every run has to reach that summary, or a CI job would go green on a machine where
    nothing can start."""
    from tendon.cli.doctor import summarise

    ceiling.write_text("safety: [broken]\n", encoding="utf-8")
    overall, _ = summarise(run_checks())

    assert overall is Status.BLOCKED
