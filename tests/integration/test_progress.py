"""The graph the roadmap measures v0.3 by, produced by the running system.

`docs/roadmap.md`: *"Done when one graph exists. x-axis: cumulative human corrections.
y-axis: intervention rate. The line goes down."*

It had been produced twice. `examples/04_improve` draws it from a script, and
`tests/integration/test_shell_loop_closes.py` shows the same fall through the real
interface with a control proving the teaching causes it. **Neither leaves anything
behind.** Nothing in the running system recorded how often it asked, so somebody
correcting a policy for a week could not see whether it was working — which is a strange
place for a project whose entire claim is a line on a chart.

## What is asserted

That episodes are logged in order with both axes, that the curve is a trailing window
rather than a cumulative average, and that it says nothing until it has enough to say. Not
the shape of any particular line: that is `test_shell_loop_closes.py`'s job, with the
control that makes it mean something.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.api.app import create_app  # noqa: E402

#: Sessions here run without a recorder: this file's subject is
#: the progress log, which is its own file and not part of a dataset.
#: A LeRobotDataset costs about thirteen seconds an episode and nothing below
#: asserts anything about one (tests/integration/conftest.py).
pytestmark = pytest.mark.usefixtures("no_recorder")
from tendon.services.progress import EpisodeRecord, append, history, rate_curve  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
STEPS = 40


def corrected_intent(context: dict) -> dict:
    intent = dict(context["intent"])
    intent["actions"] = [
        {**action, "values": [v + 0.01 for v in action["values"]]} for action in intent["actions"]
    ]
    return intent


def run_episode(client: TestClient) -> None:
    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": STEPS},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/ws/{session_id}") as socket:
        deadline = time.time() + 90
        while time.time() < deadline:
            message = socket.receive_json()
            if message.get("type") == "interrupt":
                client.post(
                    f"/api/sessions/{session_id}/decide",
                    json={
                        "resolution": "corrected",
                        "correction": corrected_intent(message["context"]),
                    },
                )
            if message.get("type") == "finished":
                break


@pytest.fixture(scope="module")
def after_two_episodes(tmp_path_factory):
    root = tmp_path_factory.mktemp("progress")
    app = create_app(
        skill_root=REPO / "skills",
        episode_root=root / "episodes",
        memory_root=root / "memory",
        progress_root=root / "progress",
    )
    client = TestClient(app)
    run_episode(client)
    run_episode(client)
    return client, root


# ------------------------------------------------------- it is written as it happens


def test_each_finished_episode_is_logged(after_two_episodes) -> None:
    _, root = after_two_episodes
    records = history(root / "progress", "grasp/cube-sim", "mujoco:so_arm100_cube")

    assert len(records) == 2


def test_a_record_carries_both_axes(after_two_episodes) -> None:
    """How often it asked, and how much it had been taught by then. One without the other
    is half a graph."""
    _, root = after_two_episodes
    record = history(root / "progress", "grasp/cube-sim", "mujoco:so_arm100_cube")[0]

    assert record.steps > 0
    assert record.interventions >= 0
    assert record.corrections_known >= 0


def test_the_log_names_itself(after_two_episodes) -> None:
    """Skill and body are in the records, not only in the filename.

    The filename is sanitised — `grasp/cube-sim` becomes `grasp_cube-sim` — and recovering
    the original from it means guessing which underscore used to be a slash, which breaks
    the first time a skill has an underscore in its name.
    """
    _, root = after_two_episodes
    record = history(root / "progress", "grasp/cube-sim", "mujoco:so_arm100_cube")[0]

    assert record.skill == "grasp/cube-sim"
    assert "so_arm100" in record.body


def test_the_endpoint_reports_it(after_two_episodes) -> None:
    client, _ = after_two_episodes
    body = client.get("/api/progress").json()

    assert len(body) == 1
    assert body[0]["skill"] == "grasp/cube-sim"
    assert body[0]["episodes"] == 2


def test_nothing_run_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    client = TestClient(create_app(skill_root=REPO / "skills", progress_root=tmp_path / "progress"))
    response = client.get("/api/progress")

    assert response.status_code == 200
    assert response.json() == []


# --------------------------------------------------------------------- the curve


def make(index: int, *, interrupted: bool, corrections: int) -> EpisodeRecord:
    return EpisodeRecord(
        skill="grasp/cube-sim",
        body="mujoco",
        episode_id=str(index),
        ended_at="2026-01-01T00:00:00+00:00",
        steps=100,
        interventions=1 if interrupted else 0,
        corrections=1 if interrupted else 0,
        corrections_known=corrections,
    )


def test_the_curve_says_nothing_until_it_has_a_window() -> None:
    """A rate over three episodes is not a rate. Drawing one invites reading a trend off
    noise, and the view says how many more episodes are needed instead."""
    records = tuple(make(i, interrupted=True, corrections=i) for i in range(4))

    assert rate_curve(records, window=10) == ()


def test_the_curve_is_a_trailing_window_not_a_cumulative_average(tmp_path: Path) -> None:
    """A cumulative rate is dominated by the early episodes and keeps falling after
    improvement stops, which makes the line look right for the wrong reason.

    Five interrupted episodes then five clean ones must reach zero on a trailing window of
    five. A cumulative average would still read 50%.
    """
    records = tuple(make(i, interrupted=i < 5, corrections=min(i + 1, 5)) for i in range(10))
    points = rate_curve(records, window=5)

    assert points
    assert points[0][1] == pytest.approx(1.0)
    assert points[-1][1] == pytest.approx(0.0)


def test_a_truncated_line_does_not_lose_the_file(tmp_path: Path) -> None:
    """The log is appended to by a running system that can be killed mid-write, so a
    half-written last line is an ordinary thing to find. Losing a week of history over one
    would be absurd."""
    root = tmp_path / "progress"
    append(root, "grasp/cube-sim", "mujoco", make(0, interrupted=True, corrections=1))

    from tendon.services.progress import progress_path

    path = progress_path(root, "grasp/cube-sim", "mujoco")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"episode_id": "1", "steps"')

    assert len(history(root, "grasp/cube-sim", "mujoco")) == 1
