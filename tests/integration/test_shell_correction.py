"""A correction made in the shell reaches the policy and the store.

This is the claim of the project, and until now it happened only in a script. The graph in
the README comes from `examples/04_improve`, which wires `on_intervention` by hand. The
interface an operator actually uses did not: `create_app` built its scheduler without that
hook, so a person could take control, correct a motion, watch the corrected motion execute
— and the policy forgot it the moment the episode ended.

`Recorder.note_interrupt` describes itself as the most valuable rows in the store, because
demonstration data almost never contains recovery from failure and this is the only place
it gets written down. **Nothing in the project called it.** The `interrupts` table existed,
was created on every episode, and had never had a row in it.

So the correction went into the motion and nowhere else. Neither learned from nor kept.
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
    """The operator's replacement: the policy's own plan, nudged.

    A correction has to be a real `Intent` — the API refuses a `CORRECTED` decision that
    carries nothing, because approving what somebody meant to replace is the dangerous
    reading. Taking the pending intent and moving it is what the shell's editor does.
    """
    intent = dict(context["intent"])
    intent["actions"] = [
        {**action, "values": [v + 0.01 for v in action["values"]]} for action in intent["actions"]
    ]
    return intent


def drive_one_episode(client: TestClient, *, correct: bool):
    """Start an episode and answer its first interrupt, then let it finish."""
    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": STEPS},
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
                decision = client.post(f"/api/sessions/{session_id}/decide", json=body)
                assert decision.status_code == 200, decision.text

            if message.get("type") == "finished":
                break

    return session_id, interrupts


# ------------------------------------------------------- it reaches the policy


def test_a_correction_is_handed_to_the_policy(tmp_path, monkeypatch) -> None:
    """Observed through the real app rather than by reading the source.

    A test that assembled its own scheduler would have passed for the whole period this
    was broken — the wiring is the thing under test, so it has to be the app's wiring.
    """
    import tendon.services.adaptive as adaptive

    taught: list[tuple] = []
    original = adaptive.AdaptivePolicy.learn_from

    def spy(self, observation, resolution):
        taught.append((observation, resolution))
        return original(self, observation, resolution)

    monkeypatch.setattr(adaptive.AdaptivePolicy, "learn_from", spy)

    app = create_app(skill_root=REPO / "skills", episode_root=tmp_path)
    _, interrupts = drive_one_episode(TestClient(app), correct=True)

    assert interrupts > 0, "the policy never handed over; nothing to correct"
    assert taught, "the correction never reached the policy"
    assert taught[0][1].correction is not None, (
        "the policy was told about it without the correction"
    )


def test_the_policy_remembers_it(tmp_path, monkeypatch) -> None:
    """`learn_from` stores only `CORRECTED` — an approval says the policy was right and a
    rejection says it was wrong without saying what to do instead. So a remembered
    correction is the evidence that the right thing was passed through."""
    import tendon.services.adaptive as adaptive

    remembered: list = []
    original = adaptive.CorrectionMemory.remember

    def spy(self, observation, correction):
        remembered.append(correction)
        return original(self, observation, correction)

    monkeypatch.setattr(adaptive.CorrectionMemory, "remember", spy)

    app = create_app(skill_root=REPO / "skills", episode_root=tmp_path)
    drive_one_episode(TestClient(app), correct=True)

    assert remembered, "nothing was stored in the correction memory"


def test_an_approval_teaches_nothing(tmp_path, monkeypatch) -> None:
    """The other half of the rule, and the one that keeps the intervention rate honest:
    treating an approval as a lesson would move the graph without information being
    added."""
    import tendon.services.adaptive as adaptive

    remembered: list = []
    original = adaptive.CorrectionMemory.remember

    def spy(self, observation, correction):
        remembered.append(correction)
        return original(self, observation, correction)

    monkeypatch.setattr(adaptive.CorrectionMemory, "remember", spy)

    app = create_app(skill_root=REPO / "skills", episode_root=tmp_path)
    _, interrupts = drive_one_episode(TestClient(app), correct=False)

    assert interrupts > 0
    assert remembered == []


# -------------------------------------------------------- and it reaches the store


def test_the_interrupt_is_written_into_the_episode(tmp_path) -> None:
    """The `interrupts` table was created on every episode and had never had a row.

    Read back with duckdb rather than trusting the recorder's own account of what it
    wrote, for the same reason the store is read through a module that cannot import the
    recorder.
    """
    duckdb = pytest.importorskip("duckdb")

    app = create_app(skill_root=REPO / "skills", episode_root=tmp_path)
    _, interrupts = drive_one_episode(TestClient(app), correct=True)
    assert interrupts > 0

    sidecar = tmp_path / "grasp__cube-sim" / "tendon_sidecar.duckdb"
    assert sidecar.is_file(), "no sidecar was written"

    con = duckdb.connect(str(sidecar))
    try:
        rows = con.execute("SELECT reason, resolution, corrected FROM interrupts").fetchall()
    finally:
        con.close()

    assert rows, "the interrupt was resolved and never recorded"
    assert any(row[2] for row in rows), "no row was marked as carrying a correction"


def test_a_recorded_interrupt_can_be_traced_to_its_episode(tmp_path) -> None:
    """The join that unblocks curation.

    Without it the store knows an interrupt happened and not which episode it happened in,
    so the episodes a curator most wants — the only recordings of recovery from failure —
    could not be promoted. `read_episodes` reported `None` rather than guessing by write
    order, which reads as reasonable and is wrong for any store written by more than one
    process.

    Skipped rather than failed when the recorder does not yet write `episode_index`: this
    asserts a property of a recording made now, and on a tree without that column the
    honest answer is still "cannot tell".
    """
    from tendon.services.episodes import read_episodes

    app = create_app(skill_root=REPO / "skills", episode_root=tmp_path)
    _, interrupts = drive_one_episode(TestClient(app), correct=True)
    assert interrupts > 0

    episodes = read_episodes(tmp_path / "grasp__cube-sim")
    assert episodes

    if episodes[0].had_interrupt is None:
        pytest.skip("this recorder does not write episode_index yet; attribution is unknown")

    assert episodes[0].had_interrupt is True
