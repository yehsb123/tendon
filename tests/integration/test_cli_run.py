"""`tendon run` is the v0.1 acceptance test, so it gets one.

The milestone reads: *`tendon run` executes a policy in simulation and episodes appear in
LeRobotDataset format without any collection flag being set.* The command did the first
half. It built a `Bus`, handed it to the scheduler, and nothing ever subscribed — so every
run completed, printed a tidy table, and left the store empty. `tendon episodes` said
"nothing recorded" immediately afterwards, and nothing anywhere failed.

The tests here are about the second half, and they are deliberately about the store rather
than about the command's output. A command that prints "recorded" is easy to write.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.cli.main import app  # noqa: E402  (after the skips: importing it needs neither)
from tendon.services.store import list_datasets  # noqa: E402

RUNNER = CliRunner()

#: Short. This drives MuJoCo and then encodes an episode, and the questions here are all
#: answered by the first frame.
STEPS = 60


def invoke(*args: str):
    return RUNNER.invoke(app, ["run", *args])


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Path:
    """One recorded run, shared by the assertions below."""
    root = tmp_path_factory.mktemp("cli-store")
    result = invoke("grasp/cube-sim", "--steps", str(STEPS), "--store", str(root))

    assert result.exit_code == 0, result.output
    return root


# --------------------------------------------------------------- it records now


def test_an_episode_appears_in_the_store(store: Path) -> None:
    """The milestone, in one line."""
    datasets = list_datasets(store)

    assert len(datasets) == 1
    assert datasets[0].episodes == 1


def test_it_is_filed_under_the_skill_that_was_run(store: Path) -> None:
    """Not the recorder's default `tendon/local`. The store's column says "skill", and a
    training run has no other way to ask for the episodes of one skill."""
    assert list_datasets(store)[0].ref == "grasp/cube-sim"


def test_the_dataset_is_readable(store: Path) -> None:
    """A half-written dataset has a directory and a size too. Readability is what
    separates "recorded" from "left something on the disk"."""
    dataset = list_datasets(store)[0]

    assert dataset.readable, dataset.unreadable_because
    assert dataset.size_bytes > 0


def test_no_flag_asked_for_any_of_this(store: Path) -> None:
    """Design decision 1. The invocation in the fixture passes `--steps` and `--store`;
    nothing about whether to record, because there is nothing to pass."""
    source = (Path(__file__).resolve().parents[2] / "src/tendon/cli/main.py").read_text(
        encoding="utf-8"
    )

    assert "--record" not in source
    assert "--no-record" not in source


# ------------------------------------------------------ and says so accurately


def test_the_output_names_where_the_episode_went(store: Path) -> None:
    """A run that recorded and a run that did not used to print identically."""
    result = invoke("grasp/cube-sim", "--steps", str(STEPS), "--store", str(store))

    assert result.exit_code == 0, result.output
    assert "recorded to" in result.output


def test_a_second_run_appends_rather_than_starting_over(store: Path) -> None:
    """Three hundred single-episode datasets are not a training set.

    Runs after the fixture's, so the count includes it and every run above.
    """
    before = list_datasets(store)[0].episodes
    assert before is not None

    result = invoke("grasp/cube-sim", "--steps", str(STEPS), "--store", str(store))
    assert result.exit_code == 0, result.output

    after = list_datasets(store)[0].episodes
    assert after == before + 1
    assert len(list_datasets(store)) == 1


# ------------------------------------------------------------ and the ref works


def test_the_documented_reference_form_runs(tmp_path: Path) -> None:
    """`tendon run grasp/cube-sim` — what the README, the shell and the command's own
    output all call this skill. It used to be the one form that did not work: only
    `skills/grasp/cube-sim` resolved, and typing the documented one got `no skill file at
    grasp\\cube-sim`, an error about paths for somebody who was not thinking about paths."""
    result = invoke("grasp/cube-sim", "--steps", "10", "--store", str(tmp_path))
    assert result.exit_code == 0, result.output


def test_a_path_still_works(tmp_path: Path) -> None:
    """The form that always worked. A reference is an addition, not a replacement."""
    result = invoke("skills/grasp/cube-sim", "--steps", "10", "--store", str(tmp_path))
    assert result.exit_code == 0, result.output


def test_an_unknown_skill_says_both_places_it_looked() -> None:
    result = invoke("grasp/not-a-skill", "--steps", "10")

    assert result.exit_code == 1
    assert "grasp/not-a-skill" in result.output or "not-a-skill" in result.output
    assert "skills" in result.output


# --------------------------------------------------- and fails when it must


def test_a_run_whose_recorder_dies_does_not_exit_zero(tmp_path: Path, monkeypatch) -> None:
    """The bus isolates a failing subscriber so a body never stops moving because of a
    consumer. That is right for the kernel and wrong for a command: the run collected
    nothing, and a status of zero tells every script reading it the opposite.

    This is exactly how the width mismatch hid — the recorder died at step 0 of every run
    on a body with a gripper, and `tendon run` exited zero having written an empty dataset.
    """
    import tendon.services.recorder as recorder_module

    class DyingRecorder(recorder_module.Recorder):
        def _on_step(self, record) -> None:  # noqa: ANN001
            raise RuntimeError("disk full")

    monkeypatch.setattr(recorder_module, "Recorder", DyingRecorder)

    result = invoke("grasp/cube-sim", "--steps", "10", "--store", str(tmp_path))

    assert result.exit_code == 1, result.output
    assert "disk full" in result.output
