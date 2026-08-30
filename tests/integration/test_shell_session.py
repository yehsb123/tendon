"""An episode started from the shell streams, and is kept.

Two faults lived in one place. `create_app`'s scheduler factory took an `on_step` callback
and dropped it — the session builds a `state` message out of every control step and the
shell has a case for it, but nothing ever sent one, so a running episode moved a body while
the view that exists to watch it stayed still. And with no bus there was no recorder either,
so `Episodes` kept saying "Nothing recorded yet" — under a paragraph telling the reader that
every run is recorded and that starting one from `Live` would make it appear there.

That paragraph was the clearest statement of design decision 1 anywhere in the interface,
and it was false. The third place the same hole turned up, after `tendon run` and
`tendon eval`, and the most visible one.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.api.app import create_app  # noqa: E402
from tendon.services.store import list_datasets  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
STEPS = 40


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("shell-store")


@pytest.fixture(scope="module")
def finished(store: Path):
    """Start one episode through the API and wait for it to end.

    Driven through the real app rather than the session class directly: the wiring being
    tested lives in `create_app`, and a test that built its own scheduler would have
    passed throughout the period the shell was silent.
    """
    app = create_app(skill_root=REPO / "skills", episode_root=store)
    client = TestClient(app)

    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": STEPS},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    # Connected after starting, which is what the shell does too — the socket address
    # contains the session id, so there is nothing to subscribe to until one exists.
    # Events queue up in the meantime rather than being lost.
    with client.websocket_connect(f"/ws/{session_id}") as socket:
        messages = []
        deadline = time.time() + 120
        while time.time() < deadline:
            message = socket.receive_json()
            messages.append(message)

            if message.get("type") == "interrupt":
                # What an operator does. The policy hands over when its samples disagree,
                # and waits five minutes for an answer — so a test that never answers is a
                # test that hangs, and one that shortens the timeout only ever measures an
                # abort. Approving keeps the episode going to its own end.
                client.post(
                    f"/api/sessions/{session_id}/decide",
                    json={"resolution": "approved"},
                )

            if message.get("type") == "finished":
                break

    state = client.get(f"/api/sessions/{session_id}").json()
    return messages, state


# --------------------------------------------------------------- it streams now


def test_the_shell_is_told_about_steps(finished) -> None:
    """The message the shell has a handler for, which nothing was sending."""
    messages, _ = finished
    states = [m for m in messages if m.get("type") == "state"]

    assert states, "no state messages arrived; on_step is not wired to the bus"


def test_a_state_message_carries_what_the_view_needs(finished) -> None:
    """Commanded and applied both, because they differ whenever the body clipped and the
    shell draws what actually happened."""
    messages, _ = finished
    first = next(m for m in messages if m.get("type") == "state")

    assert "observation" in first
    assert "commanded" in first
    assert "applied" in first


def test_the_steps_arrive_in_order(finished) -> None:
    """Dropped under load rather than queued without bound, so gaps are expected. Going
    backwards is not: it would mean the shell drew a stale position over a newer one."""
    messages, _ = finished
    steps = [m["step"] for m in messages if m.get("type") == "state"]

    assert steps == sorted(steps)


def test_the_episode_finishes(finished) -> None:
    _, state = finished
    assert state["finished"] is True
    assert state["steps"] > 0


# ------------------------------------------------------------- and it is kept


def test_the_episode_reaches_the_store(store: Path, finished) -> None:
    """What `Episodes` promises in so many words."""
    datasets = list_datasets(store)

    assert len(datasets) == 1
    assert datasets[0].episodes == 1
    assert datasets[0].readable, datasets[0].unreadable_because


def test_it_is_filed_where_the_command_line_would_file_it(store: Path, finished) -> None:
    """An episode run from the shell and one run from `tendon run` are the same kind of
    thing, and splitting them across two datasets by accident would be found much later,
    by whoever wondered why half the data was missing."""
    assert list_datasets(store)[0].ref == "grasp/cube-sim"


def test_the_shell_says_this_happens_and_now_it_does() -> None:
    """The claim in the interface, checked against the behaviour above.

    Held as a test because the sentence is the promise a reader acts on. If the recording
    path is ever removed again, this should fail next to the tests that prove it worked,
    rather than leaving the paragraph standing on its own.
    """
    view = (REPO / "shell/src/views/Episodes.tsx").read_text(encoding="utf-8")

    assert "Every run is recorded" in view
    assert "Live" in view
