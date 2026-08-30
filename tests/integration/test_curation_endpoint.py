"""Curation, where a reviewer can actually see it.

`curator.ScoredEpisode.reasons` describes itself as *"shown in the shell, because a bare
number gives a reviewer nothing to disagree with"*. There was no shell view. The scores
were computed and the reasons were written, and the only way to read either was a command
— which is not where the person deciding what to keep is sitting.

## What is checked here, and what is checked in the shell

This file covers the endpoint: that it ranks, that an empty store is an ordinary answer
rather than a 404, and that it reports when the ordering is incomplete. The rendering is
the shell's own tests and its typecheck.

The ranking arithmetic itself lives in `services/episodes.rank_episodes`, called by both
the command and this endpoint. It is one function because a second copy is how the command
and the interface would eventually disagree about what a score means — which had already
happened once with the baseline policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.api.app import create_app  # noqa: E402
from tendon.cli.main import app as cli  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RUNNER = CliRunner()


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("curation-store")
    result = RUNNER.invoke(
        cli,
        ["eval", "grasp/cube-sim", "--episodes", "3", "--steps", "50", "--store", str(root)],
    )
    assert result.exit_code == 0, result.output
    return root


@pytest.fixture
def client(store: Path) -> TestClient:
    return TestClient(create_app(skill_root=REPO / "skills", episode_root=store))


def get(client: TestClient, **params):
    return client.get("/api/skills/grasp/cube-sim/curation", params=params)


# ------------------------------------------------------------------- it ranks


def test_every_recorded_episode_is_scored(client: TestClient) -> None:
    body = get(client).json()

    assert len(body["episodes"]) == 3
    assert all(0.0 <= e["score"] <= 1.0 for e in body["episodes"])


def test_each_score_comes_with_its_reasons(client: TestClient) -> None:
    """The field the curator says exists for the shell. A number a reviewer cannot argue
    with invites either blind trust or blind rejection, and both are worse than reading."""
    body = get(client).json()

    assert all("reasons" in e for e in body["episodes"])
    assert all(isinstance(e["reasons"], list) for e in body["episodes"])


def test_the_limit_is_honoured(client: TestClient) -> None:
    assert len(get(client, limit=2).json()["episodes"]) == 2


def test_it_agrees_with_the_command(client: TestClient, store: Path) -> None:
    """One ranking, two readers.

    A second copy of this arithmetic is how the command and the interface would come to
    disagree about what a score means — which has already happened once in this project,
    with the baseline policy, and was only caught because a test asserted there was one.
    """
    from tendon.services.episodes import rank_episodes

    direct = rank_episodes(store / "grasp__cube-sim")
    served = get(client).json()["episodes"]

    assert [e.episode_id for e in direct.scored] == [e["episode_id"] for e in served]
    assert [round(e.score, 6) for e in direct.scored] == [round(e["score"], 6) for e in served]


# --------------------------------------------------------- and says what it cannot


def test_it_reports_whether_interrupts_could_be_attributed(client: TestClient) -> None:
    """Interrupt episodes are the ones a curator most wants at the top, and this store can
    only sometimes say which they were. A ranking that quietly omitted them would look
    complete."""
    assert "interrupts_known" in get(client).json()


def test_a_skill_with_nothing_recorded_is_not_an_error(tmp_path: Path) -> None:
    """The normal state before anybody has run it. A 404 would make the view shout about
    something ordinary, and the shell would show an error where it should show an
    invitation."""
    client = TestClient(create_app(skill_root=REPO / "skills", episode_root=tmp_path))
    response = get(client)

    assert response.status_code == 200
    assert response.json()["episodes"] == []
