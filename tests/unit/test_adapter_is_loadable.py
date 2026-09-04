"""`tendon train` produces an adapter and `tendon run --policy adapter` runs it.

For three days the two halves faced each other with nothing between them: the trainer wrote
a 700KB adapter, the inference adapter could wrap a LeRobot policy, and no code turned the
first into the second. `--policy adapter` answered "nothing here can load a trained adapter
yet", and `skill.yaml`'s `policy.adapter` — commented in the file as the place one appears
after training — was read by nothing.

These run without lerobot, torch or peft, because CI's unit job installs none of them and a
test that only runs where an extra happens to be installed is the shape that left the viz
suite green and ungated for weeks. What they hold down is everything decidable before the
weights: which adapter, which base, and the refusals that must happen *before* minutes are
spent loading 450 million parameters.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tendon.cli.main import app
from tendon.services.policy_lerobot import PolicyError, adapter_base

RUNNER = CliRunner()
REPO = Path(__file__).resolve().parents[2]

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
{policy}safety:
  max_joint_velocity: 1.5
eval:
  episodes: 3
"""


def _skill(tmp_path: Path, *, policy: str, name: str = "s") -> str:
    directory = tmp_path / name / "grasp" / "cube-sim"
    directory.mkdir(parents=True)
    (directory / "skill.yaml").write_text(SKILL.format(policy=policy), encoding="utf-8")
    return str(directory)


def _adapter(tmp_path: Path, *, base: str = "lerobot/smolvla_base", name: str = "a") -> Path:
    directory = tmp_path / name
    directory.mkdir(parents=True)
    (directory / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": base, "peft_type": "LORA", "r": 16}),
        encoding="utf-8",
    )
    return directory


# ------------------------------------------------------------------ reading the adapter


def test_the_base_is_read_from_the_adapter_not_from_the_skill(tmp_path: Path) -> None:
    """A LoRA is a delta against particular weights. Applied to a different base it loads,
    runs, and is wrong — no exception, no warning. PEFT writes the base it was attached to;
    a `skill.yaml` is edited by a person."""
    assert adapter_base(_adapter(tmp_path, base="lerobot/act_aloha")) == "lerobot/act_aloha"


def test_an_adapter_that_does_not_name_its_base_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "nameless"
    directory.mkdir()
    (directory / "adapter_config.json").write_text(json.dumps({"r": 16}), encoding="utf-8")

    with pytest.raises(PolicyError, match="base_model_name_or_path"):
        adapter_base(directory)


def test_an_unreadable_adapter_config_says_which_file(tmp_path: Path) -> None:
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "adapter_config.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(PolicyError, match="adapter_config.json"):
        adapter_base(directory)


# --------------------------------------------------------------- what the command refuses


def test_asking_for_an_adapter_with_none_anywhere_says_how_to_get_one(tmp_path: Path) -> None:
    skill = _skill(tmp_path, policy="  base: lerobot/smolvla_base\n")

    result = RUNNER.invoke(app, ["run", skill, "--policy", "adapter", "--driver", "absent"])

    assert result.exit_code == 1
    assert "tendon train" in result.output
    assert "--adapter" in result.output


def test_a_path_with_no_adapter_in_it_names_the_file_it_wanted(tmp_path: Path) -> None:
    """The common mistake is a directory that exists — a store, a checkpoint, or the parent
    of the right one. "Not found" for a path that is plainly there reads as a bug."""
    skill = _skill(tmp_path, policy="  base: lerobot/smolvla_base\n")

    result = RUNNER.invoke(
        app,
        ["run", skill, "--policy", "adapter", "--adapter", str(tmp_path), "--driver", "absent"],
    )

    assert result.exit_code == 1
    assert "adapter_config.json" in result.output


def test_an_adapter_for_a_different_base_is_refused_rather_than_resolved(tmp_path: Path) -> None:
    """Both sides state an intention and they disagree. Picking one silently means deciding
    which of two people to ignore, and the failure it leads to has no symptom: the adapter
    attaches to nothing, the base model runs, and the report says the adapter did."""
    skill = _skill(tmp_path, policy="  base: lerobot/smolvla_base\n")
    adapter = _adapter(tmp_path, base="lerobot/act_aloha")

    result = RUNNER.invoke(
        app,
        ["run", skill, "--policy", "adapter", "--adapter", str(adapter), "--driver", "absent"],
    )

    assert result.exit_code == 1
    assert "lerobot/act_aloha" in result.output
    assert "lerobot/smolvla_base" in result.output


def test_the_skill_supplies_the_adapter_when_no_flag_does(tmp_path: Path) -> None:
    """The field `skill.yaml` documents as "a LoRA adapter appears here after `tendon
    train`", finally read by something."""
    adapter = _adapter(tmp_path, base="lerobot/act_aloha")
    skill = _skill(
        tmp_path,
        policy=f"  base: lerobot/smolvla_base\n  adapter: {adapter.as_posix()}\n",
    )

    result = RUNNER.invoke(app, ["run", skill, "--policy", "adapter", "--driver", "absent"])

    # Refused for the base mismatch, which proves the skill's path was the one opened.
    assert result.exit_code == 1
    assert "lerobot/act_aloha" in result.output


def test_an_explicit_adapter_beats_the_one_the_skill_names(tmp_path: Path) -> None:
    """The reason to type a path is that it is not the one the file names."""
    named = _adapter(tmp_path, base="lerobot/smolvla_base", name="named")
    typed = _adapter(tmp_path, base="lerobot/act_aloha", name="typed")
    skill = _skill(
        tmp_path, policy=f"  base: lerobot/smolvla_base\n  adapter: {named.as_posix()}\n"
    )

    result = RUNNER.invoke(
        app,
        ["run", skill, "--policy", "adapter", "--adapter", str(typed), "--driver", "absent"],
    )

    assert result.exit_code == 1
    assert "lerobot/act_aloha" in result.output, "the skill's adapter was used, not the flag's"


# ------------------------------------------------------------------- the loader's guards


def test_an_adapter_that_attaches_nothing_is_refused(monkeypatch, tmp_path: Path) -> None:
    """PEFT applies an adapter by name-matching `target_modules` against the model, and a
    pattern that matches nothing is not an error there — it produces a model identical to
    the base, which runs perfectly and is not what was trained.

    Mirrors the trainer's guard at the other end, where an adapter that failed to attach
    shows up as *every* parameter being trainable. Measured on the real path: 450,046,176
    parameters before and 450,788,832 after, a difference of exactly the 742,656 the
    training run reported.
    """
    import tendon.services.policy_lerobot as module

    class _Unchanged:
        def parameters(self) -> Any:
            class P:
                def numel(self) -> int:
                    return 10

            return iter([P()])

        def eval(self) -> None:
            pass

    monkeypatch.setattr(module, "_load_base", lambda repo_id, device: _Unchanged())
    monkeypatch.setitem(
        __import__("sys").modules,
        "peft",
        type(
            "peft",
            (),
            {"PeftModel": type("M", (), {"from_pretrained": staticmethod(lambda m, p: m)})},
        ),
    )

    with pytest.raises(PolicyError, match="attached nothing"):
        module.load_adapter(
            _adapter(tmp_path),
            task="pick up a cube",
            dof=5,
            control_hz=100.0,
            reference_spread=0.0,
        )


def test_train_suggests_a_command_that_run_accepts(tmp_path: Path) -> None:
    """`tendon train` ends by naming how to run what it just made. The last version of that
    line pointed at a policy which did not exist; this one is checked against the CLI's own
    answer rather than a fixed string."""
    from tendon.cli.policies import RUNNABLE_POLICIES

    assert "adapter" in RUNNABLE_POLICIES

    source = (REPO / "src" / "tendon" / "cli" / "main.py").read_text(encoding="utf-8")
    train_body = source.partition("def train(")[2].partition("\n@app.command")[0]

    assert "--policy adapter" in train_body, "train no longer says how to run the adapter"
