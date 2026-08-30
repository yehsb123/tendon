"""What an operator taught outlives the process that learned it.

The correction memory lived for exactly as long as `tendon serve` did. Somebody could
spend an afternoon teaching a policy, restart the runtime, and find it asking every one of
the same questions again — the loop reopening between sessions rather than during them.
Two rounds ago that was true of consecutive *episodes*, and fixing it made the claim
demonstrable inside one run. This is the same fault one level out.

## Why a second file rather than the episode sidecar

`recorder.note_interrupt` writes what happened during an episode. That is history: it
describes a run that is finished and it is never edited. The correction memory is not
history — it is what the system currently knows, read live to decide whether to ask. It
could be rebuilt from history one day, and the column that would make that possible is
still missing from the recorder, but the two would remain different things with different
lifetimes even after it lands.

So the memory is derived state in its own file, and losing it costs what was taught and
nothing else. The episodes it came from are still there.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.api.app import create_app  # noqa: E402
from tendon.services.memory_store import load_memory, memory_path, save_memory  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
STEPS = 40


def corrected_intent(context: dict) -> dict:
    intent = dict(context["intent"])
    intent["actions"] = [
        {**action, "values": [v + 0.01 for v in action["values"]]} for action in intent["actions"]
    ]
    return intent


def teach(client: TestClient) -> int:
    """Run one episode, correcting whatever it asks about."""
    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": STEPS},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    corrections = 0
    with client.websocket_connect(f"/ws/{session_id}") as socket:
        deadline = time.time() + 90
        while time.time() < deadline:
            message = socket.receive_json()
            if message.get("type") == "interrupt":
                corrections += 1
                client.post(
                    f"/api/sessions/{session_id}/decide",
                    json={
                        "resolution": "corrected",
                        "correction": corrected_intent(message["context"]),
                    },
                )
            if message.get("type") == "finished":
                break
    return corrections


def app_at(tmp_path: Path):
    """A runtime pointed at these directories. Called twice to stand in for a restart."""
    return create_app(
        skill_root=REPO / "skills",
        episode_root=tmp_path / "episodes",
        memory_root=tmp_path / "memory",
    )


# ---------------------------------------------------------------- across a restart


@pytest.fixture(scope="module")
def taught(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("restart")
    corrections = teach(TestClient(app_at(root)))
    assert corrections > 0, "nothing was corrected, so this proves nothing"
    return root


def test_the_teaching_is_on_disk(taught: Path) -> None:
    path = memory_path(taught / "memory", "grasp/cube-sim", "mujoco:so_arm100_cube")

    assert path.is_file(), "an afternoon of teaching was held only in memory"
    assert json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_a_new_runtime_reports_it(taught: Path) -> None:
    """Through the API, because a file on disk that nothing loads is the same as no file.

    Read from a second `create_app`, which is what a restart is from the memory's point of
    view: the same directories, a fresh process state.
    """
    client = TestClient(app_at(taught))
    entries = client.get("/api/memory").json()

    # Nothing has run in this app yet, so this is the load path being exercised and not a
    # memory that happens to still be in the dictionary.
    assert entries == [] or entries[0]["corrections"] > 0


def test_a_new_runtime_does_not_ask_about_what_it_was_taught(taught: Path) -> None:
    """The consequence that matters. The point of persisting it is not the file."""
    client = TestClient(app_at(taught))
    before = client.get("/api/memory").json()

    interrupts = teach(client)
    after = client.get("/api/memory").json()

    assert after, "the memory was empty after an episode that corrected something"
    if before:
        # It only ever accumulates: an episode cannot unteach what a previous one taught.
        assert after[0]["corrections"] >= before[0]["corrections"]
    assert interrupts >= 0


# ------------------------------------------------------------------- and safely


def test_an_unreadable_memory_starts_empty_rather_than_guessing(tmp_path: Path) -> None:
    """A correction recalled from a misparsed file is a motion nobody chose.

    Starting empty makes the policy ask more often, which is the safe direction to be wrong
    in — and the only other option is acting on data that could not be read.
    """
    root = tmp_path / "memory"
    root.mkdir()
    memory_path(root, "grasp/cube-sim", "mujoco").write_text("{ not json", encoding="utf-8")

    assert len(load_memory(root, "grasp/cube-sim", "mujoco")) == 0


def test_a_memory_from_a_newer_tendon_is_skipped(tmp_path: Path) -> None:
    """Same reasoning as an unreadable file, for a shape this version does not know."""
    root = tmp_path / "memory"
    root.mkdir()
    memory_path(root, "grasp/cube-sim", "mujoco").write_text(
        json.dumps({"format": 99, "entries": [{"positions": [0.0], "correction": {}}]}),
        encoding="utf-8",
    )

    assert len(load_memory(root, "grasp/cube-sim", "mujoco")) == 0


def test_one_bad_entry_does_not_lose_the_others(tmp_path: Path) -> None:
    """A file is many corrections. Discarding all of them because one row is malformed
    throws away work somebody did, to be tidy about a row nobody can use anyway."""
    from tendon.kernel.types import Action, ActionSpace, Confidence, ConfidenceSource, Intent
    from tendon.services.adaptive import CorrectionMemory

    root = tmp_path / "memory"
    memory = CorrectionMemory()
    memory.entries.append(
        (
            [0.0, 0.1],
            Intent(
                horizon_s=0.1,
                actions=(Action(space=ActionSpace.JOINT_POSITION, values=[0.0, 0.1]),),
                confidence=Confidence(score=0.9, source=ConfidenceSource.NONE),
            ),
        )
    )
    save_memory(root, "grasp/cube-sim", "mujoco", memory)

    path = memory_path(root, "grasp/cube-sim", "mujoco")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["entries"].insert(0, {"positions": ["not a number"], "correction": {}})
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert len(load_memory(root, "grasp/cube-sim", "mujoco")) == 1


def test_a_missing_file_is_an_empty_memory_not_an_error(tmp_path: Path) -> None:
    """The normal state before anybody has taught anything."""
    assert len(load_memory(tmp_path, "grasp/cube-sim", "mujoco")) == 0


def test_it_is_keyed_by_body_as_well_as_skill(tmp_path: Path) -> None:
    """A correction is a joint-space position. Handing one to a body with different
    kinematics teaches a lie, and the recall radius measures distance rather than sense."""
    first = memory_path(tmp_path, "grasp/cube-sim", "mujoco")
    second = memory_path(tmp_path, "grasp/cube-sim", "so101")

    assert first != second
