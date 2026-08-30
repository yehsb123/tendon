"""An episode that loses its last operator stops proposing new motion.

`SECURITY.md` stated this as an intended property and, until last round, claimed it was
implemented. It was not: `api/app.py` returned from the socket handler and the episode ran
on to its step limit with nobody able to answer if it asked for help. The document was
corrected to say so, and named this as required work before a physical body is driven from
the shell. This closes it.

## Where the stop happens, and why there

Between chunks, never inside one. The two tiers exist because deliberation is slow and
control is not; this is the deliberation tier being told to stop proposing while the
control tier finishes what it was already given. Cutting a chunk short would be the
opposite of safe — a stop that is itself a motion nobody chose, on a body that is mid-reach.

## Why "the last operator" and not "no operator"

An episode nobody has connected to yet is ordinary: the shell posts and then opens the
socket, and `tendon run` never connects at all. Stopping those would stop exactly the runs
this is meant to protect. So the condition is *somebody watched, and now nobody is* — which
is the moment an interrupt would go unanswered.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")

from tendon.api.app import create_app  # noqa: E402
from tendon.api.session import EpisodeSession  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def app_at(tmp_path: Path):
    return create_app(
        skill_root=REPO / "skills",
        episode_root=tmp_path / "episodes",
        memory_root=tmp_path / "memory",
        progress_root=tmp_path / "progress",
    )


def start(client: TestClient, *, max_steps: int) -> str:
    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": max_steps},
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def wait_until_finished(client: TestClient, session_id: str, *, seconds: float = 60) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = client.get(f"/api/sessions/{session_id}").json()
        if state["finished"]:
            return state
        time.sleep(0.05)
    raise AssertionError("the episode never finished")


# ------------------------------------------------------------------ the condition


def test_an_episode_nobody_has_watched_is_not_abandoned() -> None:
    """The ordinary case, and the one that must not be broken by this.

    `tendon run` never connects a socket. Neither does a test. Treating "no viewer" as
    "abandoned" would stop every one of them before the first chunk.
    """
    session = EpisodeSession(
        skill="grasp/cube-sim",
        body_id="mujoco",
        scheduler_factory=lambda handler, on_step: None,
        policy_factory=lambda: None,
    )

    assert session.abandoned() is None


def test_a_watched_episode_is_not_abandoned_while_watched() -> None:
    session = EpisodeSession(
        skill="grasp/cube-sim",
        body_id="mujoco",
        scheduler_factory=lambda handler, on_step: None,
        policy_factory=lambda: None,
    )
    session.watching()

    assert session.abandoned() is None


def test_it_is_abandoned_once_the_last_viewer_leaves() -> None:
    session = EpisodeSession(
        skill="grasp/cube-sim",
        body_id="mujoco",
        scheduler_factory=lambda handler, on_step: None,
        policy_factory=lambda: None,
    )
    session.watching()
    session.stopped_watching()

    assert session.abandoned() == "the last operator disconnected"


def test_one_of_two_viewers_leaving_is_not_abandonment(tmp_path: Path) -> None:
    """Two people can watch the same episode. The one who stays can still answer."""
    session = EpisodeSession(
        skill="grasp/cube-sim",
        body_id="mujoco",
        scheduler_factory=lambda handler, on_step: None,
        policy_factory=lambda: None,
    )
    session.watching()
    session.watching()
    session.stopped_watching()

    assert session.abandoned() is None


def test_the_count_never_goes_negative() -> None:
    """A handler that unwinds twice must not make the count claim somebody is present."""
    session = EpisodeSession(
        skill="grasp/cube-sim",
        body_id="mujoco",
        scheduler_factory=lambda handler, on_step: None,
        policy_factory=lambda: None,
    )
    session.watching()
    session.stopped_watching()
    session.stopped_watching()

    assert session.abandoned() == "the last operator disconnected"


# ------------------------------------------------------------------- and the stop


def test_a_disconnect_ends_the_episode_early(tmp_path: Path) -> None:
    """The property `SECURITY.md` names.

    A long episode is started, watched briefly, and the socket is closed. It must not run
    on to its step limit.
    """
    client = TestClient(app_at(tmp_path))
    session_id = start(client, max_steps=3000)

    with client.websocket_connect(f"/ws/{session_id}"):
        time.sleep(0.2)

    state = wait_until_finished(client, session_id)

    assert state["steps"] < 3000, "the episode ran on with nobody watching"


def test_the_result_says_why_it_stopped(tmp_path: Path) -> None:
    """Distinct from how an operator ended it. `state` describes a decision somebody made;
    this describes a condition that stopped new intent being issued at all, and a run cut
    short for a reason nobody recorded is indistinguishable from one that finished."""
    client = TestClient(app_at(tmp_path))
    session_id = start(client, max_steps=3000)

    with client.websocket_connect(f"/ws/{session_id}"):
        time.sleep(0.2)

    state = wait_until_finished(client, session_id)

    # Either of the two paths, because both are the same condition arriving at a different
    # moment: the scheduler declining another chunk, or a pending decision being given up
    # on. Which one fires depends on whether the policy had raised its hand yet, and an
    # operator reading a short run needs a reason in both cases.
    assert state["stopped_because"] is not None
    assert "the last operator disconnected" in state["stopped_because"]


def test_an_unwatched_episode_still_runs_to_completion(tmp_path: Path) -> None:
    """The negative that keeps the feature honest.

    If "abandoned" were computed as "no viewer", this episode — never connected to — would
    stop at zero steps, and the test above would still pass.
    """
    client = TestClient(app_at(tmp_path))
    session_id = start(client, max_steps=30)

    state = wait_until_finished(client, session_id)

    assert state["steps"] == 30
