"""What the policy has learned, made visible.

The shell could already show that the policy asks less often. It could not show why. From
the operator's seat "it learned what I taught it" and "it happened not to ask this time"
look identical — which leaves the one claim this project rests on unverifiable by the
person best placed to check it, while they are sitting in front of it.

`GET /api/memory` reports what is held, per skill and body, including the joint positions
each correction was given at. Those positions are the actual index `CorrectionMemory.recall`
searches, not a summary of it, so an operator can see whether the thing that stopped it
asking is anywhere near where they are.

It is reported from the live memory rather than from the store, because the store does not
have it: `note_interrupt` records *that* a correction happened, not what it was. That is
also why none of this survives a restart yet.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.api.app import create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
STEPS = 40


def corrected_intent(context: dict) -> dict:
    intent = dict(context["intent"])
    intent["actions"] = [
        {**action, "values": [v + 0.01 for v in action["values"]]} for action in intent["actions"]
    ]
    return intent


def run_episode(client: TestClient, *, correct: bool) -> int:
    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": STEPS},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    interrupts = 0
    with client.websocket_connect(f"/ws/{session_id}") as socket:
        deadline = time.time() + 90
        while time.time() < deadline:
            message = socket.receive_json()
            if message.get("type") == "interrupt":
                interrupts += 1
                body = (
                    {"resolution": "corrected", "correction": corrected_intent(message["context"])}
                    if correct
                    else {"resolution": "approved"}
                )
                client.post(f"/api/sessions/{session_id}/decide", json=body)
            if message.get("type") == "finished":
                break
    return interrupts


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(skill_root=REPO / "skills", episode_root=tmp_path))


def test_nothing_taught_is_an_empty_list_not_an_error(client: TestClient) -> None:
    """The normal state before anybody has corrected anything."""
    response = client.get("/api/memory")

    assert response.status_code == 200
    assert response.json() == []


def test_a_correction_shows_up(client: TestClient) -> None:
    interrupts = run_episode(client, correct=True)
    assert interrupts > 0, "nothing was corrected, so this proves nothing"

    entries = client.get("/api/memory").json()

    assert len(entries) == 1
    assert entries[0]["skill"] == "grasp/cube-sim"
    assert entries[0]["corrections"] > 0


def test_an_approval_shows_up_as_nothing_learned(client: TestClient) -> None:
    """The distinction the panel exists to draw. An operator who only approved has taught
    the policy nothing, and a count that moved anyway would be telling them otherwise."""
    interrupts = run_episode(client, correct=False)
    assert interrupts > 0

    entries = client.get("/api/memory").json()
    assert entries == [] or entries[0]["corrections"] == 0


def test_it_reports_where_each_correction_was_given(client: TestClient) -> None:
    """Positions rather than a count alone.

    A count says the policy learned something; it does not say whether that something is
    anywhere near the situation in front of the operator. `recall` measures joint-space
    distance, so these are the numbers that decide it.
    """
    run_episode(client, correct=True)
    entry = client.get("/api/memory").json()[0]

    assert len(entry["taught_at"]) == entry["corrections"]
    assert all(isinstance(position, list) and position for position in entry["taught_at"])
    assert entry["radius"] > 0


def test_the_count_matches_the_positions(client: TestClient) -> None:
    """Two views of one thing, reported from one place, so they cannot drift apart."""
    run_episode(client, correct=True)
    run_episode(client, correct=True)

    entry = client.get("/api/memory").json()[0]
    assert entry["corrections"] == len(entry["taught_at"])
