"""`--policy replay:` was advertised for as long as it did not work.

`ReplayPolicy` has existed and been tested since early on. Its module calls it "the fixed
baseline every evaluation needs: a run whose behaviour cannot drift, against which a learned
policy is compared". The `--policy` help offered `replay:` beside `scripted`. Nothing ever
called the class, and typing the advertised option got *"policy is not available yet"*.

The advertised format was wrong too. `replay:<episode.json>` names a file nothing in this
project writes — the store holds LeRobotDataset parquet, and has since `tendon run` learned
to record. So the option now takes a skill and an episode index from the store, which is
where recordings actually are, read through `services/episodes`.

## Why the recorded rate and not the body's

A replay played at a different rate is a different motion. `ReplayPolicy` derives its
horizon from the control rate it is given, so handing it the body's rate rather than the
recording's would make the shell draw a trajectory over the wrong span — and an operator
judges partly by how fast something is about to happen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.cli.main import app  # noqa: E402

RUNNER = CliRunner()
STEPS = 60


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> Path:
    """One real episode in a store, to replay."""
    store = tmp_path_factory.mktemp("replay-store")
    result = RUNNER.invoke(
        app, ["run", "grasp/cube-sim", "--steps", str(STEPS), "--store", str(store)]
    )
    assert result.exit_code == 0, result.output
    return store


def replay(store: Path, spec: str, *args: str):
    return RUNNER.invoke(
        app,
        ["run", "grasp/cube-sim", "--policy", spec, "--store", str(store), *args],
    )


# ------------------------------------------------------------------- it replays


def test_a_recorded_episode_plays_back(recorded: Path) -> None:
    result = replay(recorded, "replay:grasp/cube-sim#0", "--steps", "500")

    assert result.exit_code == 0, result.output
    assert "replaying" in result.output


def test_it_runs_the_steps_that_were_recorded(recorded: Path) -> None:
    """Not the step limit. A replay of sixty steps is sixty steps, and stopping at the
    limit instead would put the difference into whatever measured the run."""
    result = replay(recorded, "replay:grasp/cube-sim#0", "--steps", "500")

    assert f"{STEPS}" in result.output


def test_a_finished_replay_is_reported_as_exhausted_not_cut_short(recorded: Path) -> None:
    """`EpisodeResult.exhausted` exists for this distinction: a replay that finished and a
    replay that hit the step limit are different results, and only one of them means the
    recording ran out."""
    result = replay(recorded, "replay:grasp/cube-sim#0", "--steps", "500")

    assert "exhausted" in result.output


def test_the_skill_can_be_left_out(recorded: Path) -> None:
    """`replay:#0`, or bare `replay:`, means this skill's own recordings — which is what
    somebody re-running what they just recorded wants to type."""
    result = replay(recorded, "replay:", "--steps", "500")

    assert result.exit_code == 0, result.output
    assert "replaying" in result.output


# -------------------------------------------------------------- and says why not


def test_an_episode_that_does_not_exist_says_how_many_there_are(recorded: Path) -> None:
    result = replay(recorded, "replay:grasp/cube-sim#99")

    assert result.exit_code == 1
    assert "99" in result.output
    # How many there are, not a fixed number: every replay in this module is itself
    # recorded, so the store grows as the file runs. Pinning "1 episodes" made this a test
    # of execution order.
    assert "episodes" in result.output


def test_a_skill_with_nothing_recorded_says_what_to_do(tmp_path: Path) -> None:
    result = replay(tmp_path, "replay:grasp/cube-sim#0")

    assert result.exit_code == 1
    assert "tendon run" in result.output


def test_a_non_numeric_episode_is_refused(recorded: Path) -> None:
    result = replay(recorded, "replay:grasp/cube-sim#first")

    assert result.exit_code != 0
    assert "first" in result.output


# ------------------------------------------------------ and the help is honest


# ------------------------------------------------------- and evaluation can use it


def test_eval_can_replay(recorded: Path) -> None:
    """The command `ReplayPolicy` was written for.

    Its module calls it "the fixed baseline every evaluation needs", and `tendon eval` had
    no `--policy` at all — evaluation was the one command that could not use the thing
    described as being for evaluation.
    """
    result = RUNNER.invoke(
        app,
        [
            "eval",
            "grasp/cube-sim",
            "--episodes",
            "2",
            "--steps",
            "300",
            "--policy",
            "replay:grasp/cube-sim#0",
            "--store",
            str(recorded),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("replaying") == 2


def test_each_evaluation_episode_starts_the_recording_again(recorded: Path) -> None:
    """Rebuilt per episode rather than shared.

    A single replay carried across a sweep would play its first episode and then be
    exhausted, so every later episode would report zero steps — and an evaluation whose
    episodes get shorter as it goes is measuring its own bookkeeping.
    """
    result = RUNNER.invoke(
        app,
        [
            "eval",
            "grasp/cube-sim",
            "--episodes",
            "3",
            "--steps",
            "300",
            "--policy",
            "replay:grasp/cube-sim#0",
            "--store",
            str(recorded),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count(f"{STEPS} steps") == 3


def test_both_commands_take_the_choice_through_one_function() -> None:
    """`run` and `eval` have shipped the same bug from two copies of a policy construction
    before. Checked at the source so a third command cannot quietly grow its own."""
    source = (Path(__file__).resolve().parents[2] / "src/tendon/cli/main.py").read_text(
        encoding="utf-8"
    )

    assert source.count("def _choose_policy(") == 1
    assert source.count("_choose_policy(console, loaded, capability, policy, store)") == 2


def test_the_help_no_longer_names_a_format_nothing_writes() -> None:
    """`replay:<episode.json>` was in the help for months. Nothing in this project has ever
    written episode JSON, so somebody following it would have gone looking for a file that
    could not exist."""
    result = RUNNER.invoke(app, ["run", "--help"])

    assert "episode.json" not in result.output
    assert "replay:" in result.output
