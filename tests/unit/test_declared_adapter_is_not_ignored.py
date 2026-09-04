"""A skill that names an adapter is not quietly run without it.

`skill.yaml` carries `policy.adapter`, commented in the file itself as "a LoRA adapter
appears here after `tendon train`". `tendon train` now writes one. Nothing reads the field.

So the sequence the format invites — train, put the path where the comment says, run the
skill — produced the scripted baseline, and the only thing standing between that and the
belief you were watching your own model was one word: `via scripted`.

Silence is the defect here, not the missing loader. Running a baseline on a skill that has
weights is legitimate and is how every evaluation gets its control arm; doing it without
saying so while the file says otherwise is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tendon.cli.main import app

RUNNER = CliRunner()

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
{adapter}safety:
  max_joint_velocity: 1.5
eval:
  episodes: 3
"""


def _skill(tmp_path: Path, *, adapter: str | None) -> Path:
    directory = tmp_path / "grasp" / "cube-sim"
    directory.mkdir(parents=True)
    line = f"  adapter: {adapter}\n" if adapter else ""
    (directory / "skill.yaml").write_text(SKILL.format(adapter=line), encoding="utf-8")
    return directory


def test_the_scripted_baseline_says_it_is_not_the_adapter(tmp_path: Path, capsys) -> None:
    """Checked on `_choose_policy` rather than through `run`, which would need a body."""
    from rich.console import Console

    from tendon.cli.policies import _warn_about_an_ignored_adapter
    from tendon.services.skill import load_skill

    loaded = load_skill(str(_skill(tmp_path, adapter="/somewhere/adapter")))
    _warn_about_an_ignored_adapter(Console(), loaded)

    output = capsys.readouterr().out
    assert "not using the adapter" in output
    assert "/somewhere/adapter" in output


def test_a_skill_with_no_adapter_is_not_told_off_every_run(tmp_path: Path, capsys) -> None:
    from rich.console import Console

    from tendon.cli.policies import _warn_about_an_ignored_adapter
    from tendon.services.skill import load_skill

    loaded = load_skill(str(_skill(tmp_path, adapter=None)))
    _warn_about_an_ignored_adapter(Console(), loaded)

    assert capsys.readouterr().out == ""


def test_asking_for_the_adapter_is_answered_separately_from_a_typo(tmp_path: Path) -> None:
    """The field is real, `tendon train` fills it, and asking to run it is the obvious next
    thing. Lumping it in with a misspelling would suggest the adapter is as imaginary.

    Written against `run` and passing only because this machine has mujoco: without the sim
    extra both invocations died opening a body, and the assertion compared two identical
    "MuJoCo is not installed" strings. A skipif would have made it a test that runs where an
    extra happens to be installed, which is the shape that let the viz suite sit green and
    ungated for weeks. The name check moved ahead of `open_body` instead, so the test holds
    everywhere — and a typo no longer costs a body on the way to being told about it.

    The adapter's answer used to name `policy_lerobot.py` as where the missing loader would
    go. It is written, so the answer is now about *this* adapter — the path, which is the
    only thing left that can be wrong before the weights load.
    """
    skill = _skill(tmp_path, adapter="/somewhere/adapter")

    asked = RUNNER.invoke(app, ["run", str(skill), "--policy", "adapter", "--driver", "absent"])
    typo = RUNNER.invoke(app, ["run", str(skill), "--policy", "scriptd", "--driver", "absent"])

    assert asked.exit_code == 1
    assert typo.exit_code == 1
    assert asked.output != typo.output
    assert "adapter" in asked.output
    assert "/somewhere/adapter" in asked.output.replace("\\", "/"), "it did not name the path"
    for output in (asked.output, typo.output):
        assert "absent" not in output, "the body was consulted before the name was checked"


def test_a_policy_name_is_refused_before_a_body_is_opened(tmp_path: Path) -> None:
    """`bodies.py` argues this rule for its own refusal: "Checked before construction, not
    after... touching the hardware in order to decide whether to touch it."

    The same rule one layer up. `--driver absent` does not exist, so if the name check ran
    second the output would be about the driver; it is about the policy.
    """
    skill = _skill(tmp_path, adapter=None)

    result = RUNNER.invoke(app, ["run", str(skill), "--policy", "scriptd", "--driver", "absent"])

    assert result.exit_code == 1
    assert "scriptd" in result.output
    assert "unknown driver" not in result.output


def test_eval_refuses_a_policy_name_before_a_body_too(tmp_path: Path) -> None:
    """It opens one body and runs thirty episodes through it, so the ordering matters more
    there, not less."""
    skill = _skill(tmp_path, adapter=None)

    result = RUNNER.invoke(app, ["eval", str(skill), "--policy", "scriptd", "--driver", "absent"])

    assert result.exit_code == 1
    assert "scriptd" in result.output
    assert "unknown driver" not in result.output


def _skill_fields() -> list[str]:
    """Every field on `Skill`, asked of the class rather than listed here.

    The first version of this test named `policy_adapter` in a `parametrize` list, which
    would have let the next dead field in without a word — the failure it exists to catch
    is precisely one nobody remembered to add.
    """
    import dataclasses

    from tendon.services.skill import Skill

    return [field.name for field in dataclasses.fields(Skill)]


@pytest.mark.parametrize("field", _skill_fields())
def test_every_skill_field_is_read_by_something(field: str) -> None:
    """The property, not one instance of it.

    `policy.adapter` was parsed into `Skill` and read by nothing for months, and
    `policy.hz` would have been the next one. A configuration format that accepts a key
    and ignores it teaches people to write things that do not happen.

    "Read" is satisfied by disclosure as well as by use: a field the runtime cannot act on
    yet still passes if something says out loud that it is not being acted on. What does
    not pass is silence.
    """
    root = Path(__file__).resolve().parents[2] / "src" / "tendon"
    readers = [
        path
        for path in root.rglob("*.py")
        if path.name != "skill.py" and field in path.read_text(encoding="utf-8")
    ]

    assert readers, (
        f"{field} is parsed by services/skill.py and read nowhere else. Either use it or "
        f"say, where a person can see it, that it is being ignored."
    )
