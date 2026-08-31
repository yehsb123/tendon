"""`tendon train` calls the trainer that exists.

`services/trainer.py` was written, run on CPU against `lerobot/smolvla_base`, and tested.
The command in front of it still answered "not available yet (v0.3)" and pointed at the
module as unfinished Track A work. The capability was real and the only door to it was
bolted; a reader would conclude from the CLI that training does not work.

This is the mirror of the failure this project keeps finding — a surface that advertises
what is not there. The same test catches both, because both are the same question asked of
different ends: does the command do what the command says.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tendon.cli.main import app

RUNNER = CliRunner()


def _skill_yaml(*, policy: str) -> str:
    return f"""apiVersion: tendon/v1alpha1
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
{policy}safety:
  max_joint_velocity: 1.5
eval:
  episodes: 3
"""


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "grasp" / "cube-sim"
    directory.mkdir(parents=True)
    (directory / "skill.yaml").write_text(
        _skill_yaml(policy="policy:\n  base: lerobot/smolvla_base\n"), encoding="utf-8"
    )
    return directory


class _Recorded:
    """Stands in for `Trainer`, and records what the command decided to ask for."""

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        _Recorded.last = self

    def fine_tune(self, skill: str, episodes: list[int], **kwargs: Any) -> Any:
        self.skill = skill
        self.episodes = episodes
        self.kwargs = kwargs

        class Run:
            adapter_path = Path("adapter")
            frames = 240
            steps = kwargs.get("steps", 0)
            final_loss = 0.125
            trainable_parameters = 4_200_000
            total_parameters = 450_000_000
            trainable_fraction = 4_200_000 / 450_000_000

        return Run()


def test_train_is_no_longer_refused_by_the_command_in_front_of_it(
    skill_dir: Path, monkeypatch
) -> None:
    """The refusal named v0.3 and `services/trainer.py` as work not yet done. It is done."""
    import tendon.services.trainer as trainer_module

    monkeypatch.setattr(trainer_module, "Trainer", _Recorded)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("2", []), ("0", [])]),
    )

    result = RUNNER.invoke(app, ["train", str(skill_dir / "skill.yaml")])

    assert result.exit_code == 0, result.output
    assert "not available yet" not in result.output


def test_the_selection_is_the_curator_ordering_and_is_shown(skill_dir: Path, monkeypatch) -> None:
    """Best first, as integers, and printed. A training set chosen silently is one nobody
    can argue with afterwards, and the ranking is the only reason to trust the choice."""
    import tendon.services.trainer as trainer_module

    monkeypatch.setattr(trainer_module, "Trainer", _Recorded)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("2", []), ("0", []), ("1", [])]),
    )

    result = RUNNER.invoke(app, ["train", str(skill_dir / "skill.yaml")])

    assert result.exit_code == 0, result.output
    assert _Recorded.last.episodes == [2, 0, 1], "not the ranking, or not integers"
    assert "[2, 0, 1]" in result.output


def test_a_skill_with_no_base_policy_is_told_so_rather_than_failing_inside_the_trainer(
    tmp_path: Path,
) -> None:
    """A skill is allowed to have none — that is how a scripted baseline runs without
    weights. There is nothing to adapt, which is a sentence, not a stack trace."""
    directory = tmp_path / "grasp" / "no-policy"
    directory.mkdir(parents=True)
    (directory / "skill.yaml").write_text(_skill_yaml(policy=""), encoding="utf-8")

    result = RUNNER.invoke(app, ["train", str(directory / "skill.yaml")])

    assert result.exit_code == 1
    assert "policy.base" in result.output
    assert "Traceback" not in result.output


def test_a_trainer_failure_is_printed_rather_than_traced(skill_dir: Path, monkeypatch) -> None:
    """Every `TrainerError` already names what to do about it — a missing extra, a policy
    with no LoRA targets. A traceback on top would only bury the one useful line."""
    import tendon.services.trainer as trainer_module

    class Failing(_Recorded):
        def fine_tune(self, skill: str, episodes: list[int], **kwargs: Any) -> Any:
            raise trainer_module.TrainerError("LeRobot and torch are required")

    monkeypatch.setattr(trainer_module, "Trainer", Failing)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("0", [])]),
    )

    result = RUNNER.invoke(app, ["train", str(skill_dir / "skill.yaml")])

    assert result.exit_code == 1
    assert "LeRobot and torch are required" in result.output
    assert "Traceback" not in result.output


def test_train_does_not_suggest_a_way_to_run_the_adapter_that_does_not_exist(
    skill_dir: Path, monkeypatch
) -> None:
    """The first draft of this command ended with "try it: tendon run --policy adapter".

    There is no such policy. `_choose_policy` takes `scripted` and `replay:`; `skill.yaml`'s
    `policy.adapter` is parsed and read by nothing. Following that suggestion exits 1.

    Asserted against the CLI's real answer rather than a fixed string, so the day somebody
    implements the adapter policy this test stops holding the advice back.
    """
    import tendon.services.trainer as trainer_module

    monkeypatch.setattr(trainer_module, "Trainer", _Recorded)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("0", [])]),
    )

    result = RUNNER.invoke(app, ["train", str(skill_dir / "skill.yaml")])
    assert result.exit_code == 0, result.output

    for word in _suggested_policies(result.output):
        check = RUNNER.invoke(app, ["run", str(skill_dir / "skill.yaml"), "--policy", word])
        assert "is not available yet" not in check.output, (
            f"train suggests --policy {word}, which run refuses"
        )


def _store_with(directory: Path, features: list[str]) -> Path:
    import json

    meta = directory / "grasp__cube-sim" / "meta"
    meta.mkdir(parents=True)
    (meta / "info.json").write_text(
        json.dumps({"features": {name: {} for name in features}}), encoding="utf-8"
    )
    return directory


class _NeverRuns(_Recorded):
    """Records that `fine_tune` was reached, which for these tests it should not be."""

    reached = False

    def fine_tune(self, skill: str, episodes: list[int], **kwargs: Any) -> Any:
        _NeverRuns.reached = True
        return super().fine_tune(skill, episodes, **kwargs)


def test_an_unwritable_output_is_refused_before_the_run_not_after(
    skill_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    """`Trainer.fine_tune` creates the output directory after the training loop. That is
    the right place for it to happen and the worst place to discover it cannot: a night on
    a GPU, then nothing, because 700KB could not be written.

    Same rule as refusing a `--policy` name before opening a body, with far more at stake.
    """
    import tendon.services.trainer as trainer_module

    _NeverRuns.reached = False
    monkeypatch.setattr(trainer_module, "Trainer", _NeverRuns)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("0", [])]),
    )

    # A file where a directory has to go. `destination` is `<out>/<skill ref>`, so creating
    # it has to fail whatever the platform calls that.
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")

    result = RUNNER.invoke(app, ["train", str(skill_dir / "skill.yaml"), "--out", str(blocked)])

    assert result.exit_code == 1
    assert "cannot write the adapter" in result.output
    assert "--out" in result.output, "the way out should be in the message"
    assert not _NeverRuns.reached, "training started against an output that cannot be written"


def test_a_writable_output_is_left_ready(skill_dir: Path, tmp_path: Path, monkeypatch) -> None:
    """The probe is removed; the directory is not. It is where the adapter is about to go
    and `fine_tune` creates it anyway, so removing it to put it back seconds later would
    only add a way for the two to disagree."""
    import tendon.services.trainer as trainer_module

    monkeypatch.setattr(trainer_module, "Trainer", _Recorded)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("0", [])]),
    )

    out = tmp_path / "adapters"
    result = RUNNER.invoke(app, ["train", str(skill_dir / "skill.yaml"), "--out", str(out)])

    assert result.exit_code == 0, result.output
    destination = out / "grasp__cube-sim"
    assert destination.is_dir()
    assert list(destination.iterdir()) == [], "the write probe was left behind"


def test_a_store_with_no_video_says_so_before_a_checkpoint_is_loaded(
    skill_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    """Run for real, this took four minutes to reach `ValueError: All image features are
    missing from the batch`, raised inside the model, naming neither the store nor the
    recording. The fact is in `meta/info.json` and costs nothing to read.

    Neither end is at fault: MuJoCo renders no cameras by default because rendering costs
    milliseconds a frame, and the recorder writes the schema of what is rendered. Which
    means the default path produces data no vision-language-action policy can train on,
    and that is worth saying out loud.
    """
    import tendon.services.trainer as trainer_module

    store = _store_with(tmp_path / "store", ["observation.state", "action"])
    monkeypatch.setattr(trainer_module, "Trainer", _Recorded)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("0", [])]),
    )

    result = RUNNER.invoke(app, ["train", str(skill_dir / "skill.yaml"), "--store", str(store)])

    assert result.exit_code == 0, result.output
    assert "no camera streams" in result.output


def test_a_store_with_video_names_the_streams_rather_than_warning(
    skill_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    import tendon.services.trainer as trainer_module

    store = _store_with(
        tmp_path / "store", ["observation.state", "observation.images.wrist", "action"]
    )
    monkeypatch.setattr(trainer_module, "Trainer", _Recorded)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("0", [])]),
    )

    result = RUNNER.invoke(app, ["train", str(skill_dir / "skill.yaml"), "--store", str(store)])

    assert result.exit_code == 0, result.output
    assert "observation.images.wrist" in result.output
    assert "no camera streams" not in result.output


def test_an_unreadable_store_says_nothing_rather_than_warning(
    skill_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    """ "records no cameras" and "I could not tell" lead a reader to opposite conclusions.

    Only the first is worth a warning; guessing it from a missing file would put a false
    explanation in front of whoever is reading the real failure.
    """
    import tendon.services.trainer as trainer_module

    monkeypatch.setattr(trainer_module, "Trainer", _Recorded)
    monkeypatch.setattr(
        "tendon.services.episodes.rank_episodes",
        lambda directory, limit=None: _ranking([("0", [])]),
    )

    result = RUNNER.invoke(
        app, ["train", str(skill_dir / "skill.yaml"), "--store", str(tmp_path / "empty")]
    )

    assert result.exit_code == 0, result.output
    assert "no camera streams" not in result.output
    assert "camera streams:" not in result.output


def test_the_suggestion_check_can_actually_see_a_suggestion() -> None:
    """The test above passes when nothing is suggested, which is the point — and would
    also pass if the extractor had quietly stopped finding anything.

    It nearly did. Rich prints the flag inside backticks, so the token is ``--policy` ``
    and an equality check against `--policy` matched nothing: the guard read a real
    suggestion as no suggestion at all. A test whose failure mode is silence needs one
    that shows it still has eyes.
    """
    assert _suggested_policies("try it: tendon run grasp/cube --policy adapter") == ["adapter"]
    assert _suggested_policies("`tendon run --policy` takes scripted only") == ["takes"]
    assert _suggested_policies("nothing here") == []


def _suggested_policies(output: str) -> list[str]:
    """Whatever the output puts after `--policy`, which is what a reader would type."""
    words = output.replace("\n", " ").split()
    return [
        words[i + 1].strip("`'\",.")
        for i, word in enumerate(words[:-1])
        if word.strip("`'\",.") == "--policy" and not words[i + 1].startswith("-")
    ]


def _ranking(entries: list[tuple[str, list[str]]]) -> Any:
    class Scored:
        def __init__(self, episode_id: str, reasons: list[str]) -> None:
            self.episode_id = episode_id
            self.score = 1.0
            self.reasons = reasons

    class Ranking:
        scored = tuple(Scored(episode_id, reasons) for episode_id, reasons in entries)
        interrupts_known = True

    return Ranking()
