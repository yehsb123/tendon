"""A session gives the body back.

`create_app` opens a body per session and handed it to a thread. `body.close()` appeared
twice — once when the skill was incompatible, once when the registry refused the session —
and **not once on the path where the episode actually ran**. Every episode that worked left
one open.

In simulation that is a MuJoCo model per episode, which an operator would eventually notice
as memory. On a physical arm it is a serial port, and that failure lands somewhere else
entirely: this session finishes fine and the *next* one cannot acquire the arm. The error
appears when nothing is obviously wrong, attached to a session that did nothing wrong.

`tendon run` has always closed the body in a `finally`. The API is the same program with a
thread in the middle, and the thread is what hid it.

## Why `on_closed` and not `after_episode`

`after_episode` runs inside the episode's own `try`. A policy factory that raises means
there was no episode, so that hook never fires — and the body still needs giving back. The
outermost `finally` is the only place that runs whatever happened.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")

from tendon.api.app import create_app  # noqa: E402

#: Sessions here run without a recorder: this file's subject is
#: the body being given back, which happens whether or not anything was recorded.
#: A LeRobotDataset costs about thirteen seconds an episode and nothing below
#: asserts anything about one (tests/integration/conftest.py).
pytestmark = pytest.mark.usefixtures("no_recorder")
from tendon.api.session import EpisodeSession  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def closed(monkeypatch) -> list[str]:
    """Record every body that gets closed, through the driver everything opens."""
    import tendon.drivers.mujoco as mujoco_driver

    seen: list[str] = []
    original = mujoco_driver.MujocoDriver.close

    def spy(self) -> None:
        seen.append(getattr(self, "name", "mujoco"))
        return original(self)

    monkeypatch.setattr(mujoco_driver.MujocoDriver, "close", spy)
    return seen


def app_at(tmp_path: Path):
    return create_app(
        skill_root=REPO / "skills",
        episode_root=tmp_path / "episodes",
        memory_root=tmp_path / "memory",
        progress_root=tmp_path / "progress",
    )


def run_and_wait(client: TestClient, *, max_steps: int = 20) -> None:
    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": max_steps},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/ws/{session_id}") as socket:
        deadline = time.time() + 60
        while time.time() < deadline:
            message = socket.receive_json()
            if message.get("type") == "interrupt":
                client.post(f"/api/sessions/{session_id}/decide", json={"resolution": "approved"})
            if message.get("type") == "finished":
                break


def test_a_finished_episode_gives_the_body_back(tmp_path: Path, closed: list[str]) -> None:
    """The path that leaked. It is the only path that normally runs."""
    run_and_wait(TestClient(app_at(tmp_path)))

    assert closed, "the episode finished and the body was never closed"


def test_a_session_that_never_gets_an_episode_still_gives_it_back(tmp_path: Path) -> None:
    """The reason this hook is outside the episode rather than inside it.

    Driven through `EpisodeSession` directly: the failure being tested is a policy factory
    that raises, and the app has no way to produce one.
    """
    released: list[bool] = []

    def explode():
        raise RuntimeError("no policy today")

    session = EpisodeSession(
        skill="grasp/cube-sim",
        body_id="test",
        scheduler_factory=lambda handler, on_step: None,
        policy_factory=explode,
        on_closed=lambda: released.append(True),
    )
    session.start()
    session.join(timeout=10)

    assert session.state.error is not None, "the failure should be surfaced, not swallowed"
    assert released == [True], "the body was not given back when there was no episode"


def test_a_body_that_fails_to_close_does_not_fail_the_episode(tmp_path: Path, monkeypatch) -> None:
    """A real episode, and a close that raises after it.

    Isolation for the same reason every other hook here is isolated: the episode is the
    thing that mattered and it already happened. Written first against a session whose
    policy factory also raised, which asserted nothing — the episode had failed anyway, so
    "the close did not hide it" was true for the wrong reason.
    """
    import tendon.drivers.mujoco as mujoco_driver

    monkeypatch.setattr(
        mujoco_driver.MujocoDriver,
        "close",
        lambda self: (_ for _ in ()).throw(OSError("port stuck")),
    )

    client = TestClient(app_at(tmp_path))
    run_and_wait(client)

    sessions = client.get("/api/sessions").json()
    assert sessions
    latest = sessions[-1]
    assert latest["finished"] is True
    assert latest["steps"] > 0
    assert latest["error"] is None, "a failed close was reported as a failed episode"


def test_a_finished_session_lets_go_of_its_step_records(tmp_path: Path) -> None:
    """The other thing a session was holding for the life of the process.

    Every step's observation and both actions, about 728 bytes each, kept for data nothing
    in the API reads again. The recorder took each step off the bus as it happened and the
    progress log has the episode's line; the durable record is elsewhere and better.

    Checked through a real episode rather than a constructed result, because the clearing
    happens on the episode thread and the question is whether it happens at all.
    """
    from tendon.api.session import SessionRegistry

    registry_holder: list[SessionRegistry] = []
    original = SessionRegistry.add

    def spy(self, session):
        registry_holder.append(self)
        return original(self, session)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(SessionRegistry, "add", spy)
        client = TestClient(app_at(tmp_path))
        run_and_wait(client)
    finally:
        monkeypatch.undo()

    assert registry_holder
    sessions = registry_holder[0].all()
    assert sessions

    result = sessions[-1].state.result
    assert result is not None, "the episode did not finish"
    assert result.steps > 0, "an episode that ran no steps proves nothing here"
    assert result.records == [], "the session is still holding every step it took"


def test_two_sessions_release_two_bodies(tmp_path: Path, closed: list[str]) -> None:
    """One per session, not one for the last one. A leak that only shows up after fifty
    episodes is exactly the kind a single-run test would miss."""
    client = TestClient(app_at(tmp_path))
    run_and_wait(client)
    run_and_wait(client)

    assert len(closed) >= 2
