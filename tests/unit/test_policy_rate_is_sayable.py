"""A skill can say what rate its policy's actions were meant for.

`LeRobotPolicy` takes `policy_hz` and documents it as something "a caller that knows has to
say", because no checkpoint says it: `smolvla_base`, `act_aloha_sim_transfer_cube_human`
and `diffusion_pusht` all publish `chunk_size` and `n_action_steps` and none publishes an
fps. The caller that knows is the skill — and the skill format had nowhere to write it.

A parameter whose contract is "somebody must supply this", with nowhere to supply it from,
is how the defect it was added to fix comes back: the next caller passes nothing, the rates
are assumed equal again, and a 30 Hz policy runs more than three times too fast on a 100 Hz
body. Silently, and in proportion to how fast the body is, so a faster machine looks more
broken and the cause looks less like arithmetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from tendon.cli.main import _report_policy_rate
from tendon.services.skill import SkillError, load_skill

SKILL = """apiVersion: tendon/v1alpha1
kind: Skill
metadata:
  name: cube-sim
  namespace: grasp
  version: 0.1.0
  summary: Pick up a cube.
requires:
  dof: 5
  gripper: parallel
  action_spaces: [joint_position]
  control_hz: 50
policy:
  base: lerobot/smolvla_base
{hz}safety:
  max_joint_velocity: 1.5
eval:
  episodes: 3
"""


def _skill(tmp_path: Path, *, hz: str | None) -> str:
    # Named after the value, so one test can write two skills without colliding.
    directory = tmp_path / str(hz) / "grasp" / "cube-sim"
    directory.mkdir(parents=True)
    line = f"  hz: {hz}\n" if hz is not None else ""
    (directory / "skill.yaml").write_text(SKILL.format(hz=line), encoding="utf-8")
    return str(directory)


class _Capability:
    body_id = "test:body"
    cameras: tuple[str, ...] = ()

    def __init__(self, control_hz: float) -> None:
        self.control_hz = control_hz


def test_a_skill_can_declare_the_rate_its_policy_was_trained_for(tmp_path: Path) -> None:
    assert load_skill(_skill(tmp_path, hz="30")).policy_hz == 30.0


def test_unknown_is_the_default_and_stays_unknown(tmp_path: Path) -> None:
    """The usual case. A number invented to fill the field would be worse than the gap:
    it would be believed, and held actions would be wrong by exactly its error."""
    assert load_skill(_skill(tmp_path, hz=None)).policy_hz is None
    assert load_skill(_skill(tmp_path, hz="null")).policy_hz is None


def test_a_rate_that_cannot_be_divided_into_is_refused_at_load(tmp_path: Path) -> None:
    """The number is divided into the body's rate to decide how long to hold each action.
    A negative divisor gives a hold count that is quietly nonsense rather than an error."""
    with pytest.raises(SkillError, match="positive"):
        load_skill(_skill(tmp_path, hz="-5"))

    with pytest.raises(SkillError, match="number"):
        load_skill(_skill(tmp_path, hz="fast"))


def test_the_two_rates_are_stated_before_anything_moves(tmp_path: Path, capsys) -> None:
    loaded = load_skill(_skill(tmp_path, hz="30"))

    _report_policy_rate(Console(width=200), loaded, _Capability(100))

    output = capsys.readouterr().out
    assert "30 Hz" in output
    assert "100 Hz" in output


def test_nothing_is_said_when_the_rates_agree_or_are_unknown(tmp_path: Path, capsys) -> None:
    _report_policy_rate(Console(), load_skill(_skill(tmp_path, hz="100")), _Capability(100))
    assert capsys.readouterr().out == ""

    _report_policy_rate(Console(), load_skill(_skill(tmp_path, hz=None)), _Capability(100))
    assert capsys.readouterr().out == ""


def test_the_holding_arithmetic_is_not_duplicated_here() -> None:
    """Deciding how many ticks to hold each action belongs to `LeRobotPolicy`, which is
    where the guard against a non-whole ratio also lives.

    This project has twice shipped one bug from two copies of the same calculation, so the
    CLI states the inputs and computes nothing. If a hold count ever appears in `cli/`, the
    two copies can disagree and the one a person reads will be the wrong one.
    """
    source = (Path(__file__).resolve().parents[2] / "src/tendon/cli/main.py").read_text(
        encoding="utf-8"
    )
    rate_report = source.partition("def _report_policy_rate")[2].partition("\ndef ")[0]

    assert "policy_hz" in rate_report, "the function under test moved or was renamed"
    assert "/" not in rate_report.replace("[/dim]", ""), "a rate ratio is being computed here"
