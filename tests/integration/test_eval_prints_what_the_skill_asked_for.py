"""`eval.report` named three sections from the first version of the format and nothing
read it.

`tendon eval` printed success rate, intervention rate and failure modes unconditionally,
and the shipped `skill.yaml` happened to list exactly those three. A declaration that
cannot be wrong is not a declaration — it reads like a setting, and an author who narrowed
it would get the full report anyway with nothing saying why.

Run for real rather than read off the source: what matters is what a person sees. One
episode of ten steps, because the assertion is about which rows appear and not about what
the policy did.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")

from typer.testing import CliRunner  # noqa: E402

from tendon.cli.main import app  # noqa: E402
from tendon.services.skill import SkillError, load_skill  # noqa: E402

RUNNER = CliRunner()
REPO = Path(__file__).resolve().parents[2]
REAL = REPO / "skills" / "grasp" / "cube-sim" / "skill.yaml"


def _skill_asking_for(tmp_path: Path, sections: list[str] | None) -> Path:
    raw = yaml.safe_load(REAL.read_text(encoding="utf-8"))
    if sections is None:
        raw["eval"].pop("report", None)
    else:
        raw["eval"]["report"] = sections
    directory = tmp_path / "skill"
    directory.mkdir(exist_ok=True)
    (directory / "skill.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return directory


def _evaluate(directory: Path, store: Path) -> str:
    result = RUNNER.invoke(
        app,
        ["eval", str(directory), "--episodes", "1", "--steps", "10", "--store", str(store)],
    )
    assert result.exit_code == 0, result.output
    return result.output


def test_a_narrowed_report_prints_only_what_it_named(tmp_path: Path) -> None:
    output = _evaluate(_skill_asking_for(tmp_path, ["intervention_rate"]), tmp_path / "store")

    assert "intervention rate" in output
    assert "success rate" not in output, "the skill did not ask for it"
    assert "failure modes" not in output, "nor for these"


def test_the_counts_are_printed_whatever_the_skill_says(tmp_path: Path) -> None:
    """`episodes`, `corrections` and `faults` are not selectable and should not be. A
    report that can hide how many episodes it rests on is worse than a verbose one."""
    output = _evaluate(_skill_asking_for(tmp_path, ["intervention_rate"]), tmp_path / "store")

    for always in ("episodes", "corrections", "faults"):
        assert always in output


def test_asking_for_everything_prints_everything(tmp_path: Path) -> None:
    """The other direction, so the test above cannot pass by the rows never appearing."""
    output = _evaluate(
        _skill_asking_for(tmp_path, ["success_rate", "intervention_rate", "failure_modes"]),
        tmp_path / "store",
    )

    assert "success rate" in output
    assert "intervention rate" in output
    assert "failure modes" in output, (
        "the scripted baseline never lifts the cube, so there is a failure mode to show"
    )


def test_a_skill_that_says_nothing_gets_the_full_report(tmp_path: Path) -> None:
    """Omitting the block is not asking for silence."""
    output = _evaluate(_skill_asking_for(tmp_path, None), tmp_path / "store")

    assert "success rate" in output
    assert "intervention rate" in output


def test_a_section_nothing_can_print_is_refused_at_load(tmp_path: Path) -> None:
    """`report: [sucess_rate]` would otherwise print two sections and the author would read
    the absence of the third as "there was nothing to say"."""
    with pytest.raises(SkillError) as caught:
        load_skill(_skill_asking_for(tmp_path, ["sucess_rate"]))

    assert "sucess_rate" in str(caught.value)
    assert "success_rate" in str(caught.value), "name the one they meant"
