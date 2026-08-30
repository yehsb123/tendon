"""`tendon eval` collects, and collects the same way `tendon run` does.

`run` was fixed first, and fixing it revealed that the two commands had drifted. `eval`
constructed its own `Scheduler` with no bus at all, so an evaluation ran thirty episodes
and kept none of them — the larger hole in design decision 1, in the command that produces
the data actually worth keeping. It also built its own copy of the baseline policy, which
meant the jaw fix that made recording work on a body with a gripper existed in one of the
two places that needed it.

So the tests here are as much about the two commands agreeing as about either one working.
A second copy of a fix is a bug waiting for the next round of edits.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.cli.main import app  # noqa: E402
from tendon.services.store import list_datasets  # noqa: E402

RUNNER = CliRunner()

EPISODES = 3
STEPS = 40


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory):
    """One evaluation of a few short episodes, shared by the assertions below."""
    root = tmp_path_factory.mktemp("eval-store")
    result = RUNNER.invoke(
        app,
        [
            "eval",
            "grasp/cube-sim",
            "--episodes",
            str(EPISODES),
            "--steps",
            str(STEPS),
            "--store",
            str(root),
        ],
    )

    assert result.exit_code == 0, result.output
    return root, result


# ------------------------------------------------------------------ it collects


def test_every_episode_is_kept(evaluated) -> None:
    """The claim. An evaluation is where the episodes worth training on come from, and
    this command had no bus at all — it ran, reported, and wrote nothing."""
    root, _ = evaluated
    datasets = list_datasets(root)

    assert len(datasets) == 1
    assert datasets[0].episodes == EPISODES


def test_they_are_separate_episodes_not_one_long_one(evaluated) -> None:
    """Opened and closed around each episode. Thirty episodes concatenated into one is
    not an evaluation set — nothing downstream could tell where a run began."""
    root, _ = evaluated
    assert list_datasets(root)[0].episodes == EPISODES


def test_filed_under_the_skill(evaluated) -> None:
    root, _ = evaluated
    assert list_datasets(root)[0].ref == "grasp/cube-sim"


def test_the_report_says_where_they_went(evaluated) -> None:
    _, result = evaluated
    assert "recorded to" in result.output


def test_no_flag_asked_for_it(evaluated) -> None:
    """Design decision 1. The invocation passes `--episodes`, `--steps` and `--store`;
    nothing about whether to record."""
    _, result = evaluated
    assert "--record" not in result.output


# ------------------------------------------------- and the two commands agree


def test_eval_and_run_build_the_same_baseline_policy() -> None:
    """One constructor, not two.

    `run` gained a jaw value so a body with a gripper could be recorded at all; `eval`
    kept the old call and would have died at step 0 of every episode. Both now go through
    `_baseline_policy`, and this fails if either grows its own copy again.
    """
    source = (Path(__file__).resolve().parents[2] / "src/tendon/cli/main.py").read_text(
        encoding="utf-8"
    )

    assert source.count("ScriptedPolicy(") == 1, (
        "the baseline policy is constructed in more than one place; the last time that "
        "was true, only one of them was fixed"
    )
    # Assignments only, so the definition itself is not counted as a third caller.
    assert source.count("= _baseline_policy(") == 2


def test_both_commands_record_through_the_same_helper() -> None:
    """`_attach_recorder` decides where episodes go and what to say when LeRobot is
    missing. Two copies of that would eventually disagree about one of them."""
    source = (Path(__file__).resolve().parents[2] / "src/tendon/cli/main.py").read_text(
        encoding="utf-8"
    )

    assert source.count("Recorder(root=root") == 1
    assert source.count("_attach_recorder(console, bus, loaded, store)") == 2


# ------------------------------------------------------- and it fails honestly


def test_an_evaluation_whose_recorder_dies_does_not_exit_zero(tmp_path: Path, monkeypatch) -> None:
    """And stops trying.

    The bus drops a subscriber that raises and never re-subscribes it, so after the first
    failure the remaining episodes would record nothing while still opening and closing a
    dataset for each. Empty episodes are worse than none: they look like a run that
    happened.
    """
    import tendon.services.recorder as recorder_module

    class DyingRecorder(recorder_module.Recorder):
        def _on_step(self, record) -> None:  # noqa: ANN001
            raise RuntimeError("disk full")

    monkeypatch.setattr(recorder_module, "Recorder", DyingRecorder)

    result = RUNNER.invoke(
        app,
        ["eval", "grasp/cube-sim", "--episodes", "3", "--steps", "10", "--store", str(tmp_path)],
    )

    assert result.exit_code == 1, result.output
    assert "recording stopped" in result.output
    assert "disk full" in result.output
    # Reported once, for the episode it happened in — not once per remaining episode.
    assert result.output.count("disk full") == 1
