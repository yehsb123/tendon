"""What an operator teaches survives the episode they taught it in.

`examples/04_improve` states the requirement in its own comment — *one memory across every
episode, because what somebody taught in episode 3 has to still be there in episode 30* —
and the shell did not meet it. `create_app` built a fresh `AdaptivePolicy` for every
session, which means a fresh `CorrectionMemory`, which means a correction survived exactly
as long as the episode it was made in.

The consequence is not subtle. An operator corrects episode one, starts episode two, and
the policy asks the same question again. The intervention rate cannot fall however patient
they are, so the graph this interface exists to produce could not be produced through it.

Memory now lives on the app, keyed by skill and body. It lasts as long as `tendon serve`
does, and not yet across a restart — the corrections are in each episode's `interrupts`
table, which is what a rebuild would read, and that is a later step.
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
STEPS = 60


def corrected_intent(context: dict) -> dict:
    intent = dict(context["intent"])
    intent["actions"] = [
        {**action, "values": [v + 0.01 for v in action["values"]]} for action in intent["actions"]
    ]
    return intent


def run_episode(client: TestClient, *, correct: bool, skill: str = "grasp/cube-sim") -> int:
    """One episode, answering every interrupt. Returns how many there were."""
    response = client.post(
        "/api/sessions",
        json={"skill": skill, "body": "mujoco", "max_steps": STEPS},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    interrupts = 0
    with client.websocket_connect(f"/ws/{session_id}") as socket:
        deadline = time.time() + 120
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
def spy(monkeypatch):
    """Record the memory each session's policy was built with."""
    import tendon.services.adaptive as adaptive

    seen: list = []
    original = adaptive.AdaptivePolicy.__init__

    def capture(self, inner, memory=None):
        original(self, inner, memory)
        seen.append(self.memory)

    monkeypatch.setattr(adaptive.AdaptivePolicy, "__init__", capture)
    return seen


def test_a_second_episode_gets_the_same_memory(tmp_path, spy) -> None:
    """The requirement, stated as identity.

    Checked on the object rather than on a count, because a second memory that happened to
    contain the same number of entries would pass a count and still be a policy that
    learned nothing from the first episode.
    """
    client = TestClient(create_app(skill_root=REPO / "skills", episode_root=tmp_path))

    run_episode(client, correct=True)
    run_episode(client, correct=True)

    assert len(spy) == 2, "two sessions should have built two policies"
    assert spy[0] is spy[1]


def test_what_was_taught_in_the_first_episode_is_there_in_the_second(tmp_path, spy) -> None:
    client = TestClient(create_app(skill_root=REPO / "skills", episode_root=tmp_path))

    interrupts = run_episode(client, correct=True)
    assert interrupts > 0, "nothing was corrected, so this proves nothing"

    taught_by_the_end_of_the_first = len(spy[0])
    assert taught_by_the_end_of_the_first > 0

    run_episode(client, correct=True)
    assert len(spy[1]) >= taught_by_the_end_of_the_first


def test_a_different_skill_does_not_inherit_it(tmp_path, spy) -> None:
    """Keyed on the task as well as the body.

    A correction is a joint-space position taught for one job. Recalling it under a
    different skill would hand the operator's answer to a question nobody asked, and the
    recall radius — which only measures distance in joint space — would not notice.

    Two skills rather than two bodies because simulation offers one body, and the same
    key is doing both jobs.
    """
    root = tmp_path / "skills"
    _copy_skill(root, namespace="grasp", name="cube-sim")
    _copy_skill(root, namespace="place", name="cube-sim")

    client = TestClient(create_app(skill_root=root, episode_root=tmp_path / "store"))

    run_episode(client, correct=True, skill="grasp/cube-sim")
    run_episode(client, correct=True, skill="place/cube-sim")

    assert len(spy) == 2
    assert spy[0] is not spy[1], "one skill's corrections were handed to another"


def test_the_app_starts_sessions_from_the_root_it_was_given(tmp_path) -> None:
    """`create_app(skill_root=…)` has to mean it for sessions too.

    It did not. The discovery routes resolved under the injected root and
    `POST /api/sessions` called `load_skill` with no root, so it went to the module global
    — the working directory's `skills/`. An app pointed at a fixture directory still
    started sessions from whatever the repository happened to contain, and every test that
    used a fixture root was quietly testing the shipped skill.

    Found by trying to run a skill that exists only in a fixture, which is exactly the
    thing the injection is for.
    """
    root = tmp_path / "skills"
    _copy_skill(root, namespace="fixture", name="only-here")

    client = TestClient(create_app(skill_root=root, episode_root=tmp_path / "store"))
    response = client.post(
        "/api/sessions",
        json={"skill": "fixture/only-here", "body": "mujoco", "max_steps": 5},
    )

    assert response.status_code == 200, response.text


def test_a_skill_outside_that_root_is_refused(tmp_path) -> None:
    """The other direction. An app given a root should not reach past it into the working
    directory, or the injection is a suggestion rather than a boundary."""
    root = tmp_path / "skills"
    _copy_skill(root, namespace="fixture", name="only-here")

    client = TestClient(create_app(skill_root=root, episode_root=tmp_path / "store"))
    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": 5},
    )

    assert response.status_code == 400, response.text


def _copy_skill(root: Path, *, namespace: str, name: str) -> None:
    """Write the shipped skill into a temporary root under a different reference."""
    source = (REPO / "skills/grasp/cube-sim/skill.yaml").read_text(encoding="utf-8")
    text = source.replace("  name: cube-sim", f"  name: {name}").replace(
        "  namespace: grasp", f"  namespace: {namespace}"
    )

    directory = root / namespace / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "skill.yaml").write_text(text, encoding="utf-8")
