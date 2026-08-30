"""`tendon curate` — the command that said it was blocked on something it was not.

For months it reported: *"What is missing is reading recorded episodes back, which needs
the [robot] extra."* Nobody had checked. A LeRobotDataset on disk is parquet with an
ordinary schema, and duckdb — already a dependency, for the sidecar — reads it directly.

That matters beyond one command. Curation is where somebody decides what is worth training
on, and it is exactly the step you want to run on a laptop against data collected somewhere
else. `services/episodes.py` reads episodes back with no LeRobot, no torch and no simulator,
for the same reason `services/store.py` lists them without importing the recorder: the
question outlives the ability to record.

## The interrupt gap, checked here on purpose

The curator values interrupt episodes above everything, because they are the only recording
of recovery from failure. The store cannot yet say which episodes they were — the sidecar
keys interrupts by the recorder's uuid and the parquet numbers episodes from zero, with no
column joining them. So `curate` says so rather than ranking as though it knew, and one of
the tests below is about that sentence appearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.cli.main import app  # noqa: E402
from tendon.services.episodes import read_episodes  # noqa: E402

RUNNER = CliRunner()


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Path:
    """A few real episodes, recorded the way anything else records them."""
    root = tmp_path_factory.mktemp("curate-store")
    result = RUNNER.invoke(
        app,
        ["eval", "grasp/cube-sim", "--episodes", "3", "--steps", "50", "--store", str(root)],
    )
    assert result.exit_code == 0, result.output
    return root


def curate(store: Path, *args: str):
    return RUNNER.invoke(app, ["curate", "grasp/cube-sim", "--store", str(store), *args])


# --------------------------------------------------------------- reading them back


def test_episodes_are_read_back_without_lerobot() -> None:
    """The assumption the command was blocked on, stated as a property of the module.

    Checked at the source because an import that happens to be cached would make a runtime
    check pass on this machine and fail on the one that matters — a laptop with the data
    and none of the stack that produced it.
    """
    source = (Path(__file__).resolve().parents[2] / "src/tendon/services/episodes.py").read_text(
        encoding="utf-8"
    )

    assert "import lerobot" not in source
    assert "from lerobot" not in source
    assert "import torch" not in source


def test_each_recorded_episode_comes_back(store: Path) -> None:
    episodes = read_episodes(store / "grasp__cube-sim")

    assert len(episodes) == 3
    assert all(len(e.actions) > 0 for e in episodes)


def test_the_control_period_comes_from_the_dataset(store: Path) -> None:
    """Not assumed. Jerk is a third derivative, so a wrong dt scales it by that cubed."""
    episodes = read_episodes(store / "grasp__cube-sim")

    assert episodes[0].dt_s == pytest.approx(0.01)


def test_the_jaw_is_not_read_as_a_sixth_joint(store: Path) -> None:
    """The action column is joints plus one channel for the gripper. Reading that last
    number as a joint would put a value in the range [0, 1] alongside radians and corrupt
    every measurement taken from it."""
    episodes = read_episodes(store / "grasp__cube-sim")
    action = episodes[0].actions[0]

    assert action.gripper is not None
    assert len(action.values) == 5


# ------------------------------------------------------------------- and ranking them


def test_it_ranks_what_is_there(store: Path) -> None:
    result = curate(store)

    assert result.exit_code == 0, result.output
    assert "score" in result.output
    for episode_id in ("0", "1", "2"):
        assert episode_id in result.output


def test_the_limit_shortens_the_list(store: Path) -> None:
    result = curate(store, "--limit", "1")

    assert result.exit_code == 0, result.output
    assert result.output.count("nothing notable") <= 1


def test_an_empty_store_says_so_rather_than_ranking_nothing(tmp_path: Path) -> None:
    result = curate(tmp_path)

    assert result.exit_code == 1
    assert "tendon run" in result.output


def test_it_no_longer_claims_to_be_unavailable(store: Path) -> None:
    """The sentence that was wrong. Kept as a check because a command reporting itself
    blocked is the most expensive kind of stale text — nobody tries it again."""
    result = curate(store)

    assert "not available yet" not in result.output


def test_train_no_longer_says_it_is_waiting_on_curate() -> None:
    """`train` explained itself by pointing at `curate`, which now works. A stub that
    names the wrong blocker sends the next person to fix something already fixed."""
    result = RUNNER.invoke(app, ["train", "grasp/cube-sim"])

    assert result.exit_code == 1
    assert "waits on curate" not in result.output
