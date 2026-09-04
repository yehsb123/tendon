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
    import ast

    from tests import cli_source

    source = cli_source.source()

    # Every command goes through `choose_policy`, which decides between the scripted
    # baseline and a replay and then calls the builder. That is a stronger version of the
    # same property: no command names a policy constructor at all, so none can grow its
    # own idea of what "the baseline" is.
    #
    # Three of them: `run`, `eval`, and `calibrate` — which measures a policy and so has to
    # build the same one the run under test would use, or it measures something else.
    assert len(cli_source.calls_to("choose_policy")) == 3

    # And the builder is reached only through it. A command calling it directly is how the
    # two copies came apart the first time.
    assert cli_source.callers_of("_baseline_policy") == {"choose_policy"}

    # Where policies are actually built. This once asserted `ScriptedPolicy(` appeared
    # exactly once, which was a proxy for the real property and stopped being true the
    # moment a second baseline was added for a legitimate reason. The property is that no
    # *command* builds one: they ask `_baseline_policy` and it decides.
    builders: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                # Both baselines. `FunctionPolicy` was called `ScriptedPolicy` until it
                # collided with the class of that name in `policy_scripted`, which this
                # file imported twenty lines away — a traceback naming `ScriptedPolicy`
                # said nothing about which one had raised.
                and inner.func.id in {"FunctionPolicy", "ScriptedPolicy"}
            ):
                builders.add(node.name)

    assert builders == {"_baseline_policy", "_named_baseline"}, (
        f"a policy is constructed in {sorted(builders)}; the last time a command built its "
        "own, only one of the two copies was fixed"
    )


def test_both_commands_record_through_the_same_helper() -> None:
    """`_attach_recorder` decides where episodes go and what to say when LeRobot is
    missing. Two copies of that would eventually disagree about one of them.

    Counted by parsing rather than by matching the call text, which is what this did and
    what broke it: adding the `body` argument the frames source needs changed the string
    and the test read two callers as none. The property is how many places call the
    helper, and an argument list is not part of that.
    """
    from tests import cli_source

    assert cli_source.source().count("Recorder(root=root") == 1

    calls = cli_source.calls_to("attach_recorder")
    assert len(calls) == 2, f"{len(calls)} commands attach a recorder; run and eval are the two"


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
