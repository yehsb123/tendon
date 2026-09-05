"""The shell's declared response shapes match what the runtime actually sends.

Two hand-maintained halves with nothing between them. `api/app.py` returns bare
`dict[str, Any]` built from string literals in each handler; `shell/src/api/client.ts`
declares an interface per response, written by hand to match. Nothing checks that they do.

The API's own docstring names the failure: "a shell built against a different contract is
the failure that looks like a bug everywhere else". A renamed key does not break a Python
test, does not break a TypeScript build — `Any` on one side, a hand-written interface on
the other — and surfaces as a blank field on a page, at which point the search starts
anywhere except the two lines that disagree.

Checked against a live response rather than by reading `app.py`, because what matters is
what is sent, and the handlers build their dicts inline.

Only the dangerous direction is asserted: every non-optional field the shell declares must
arrive. A response carrying *more* than the shell reads is allowed — the runtime is not
only spoken to by this shell, and `/api/skills` currently sends five fields the list view
does not use.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
CLIENT_TS = REPO / "shell" / "src" / "api" / "client.ts"


def _declared(interface: str) -> dict[str, bool]:
    """Field name -> whether it is optional, from a TypeScript interface.

    A parser rather than a copy of the field names here, so that this checks the file the
    shell is compiled from and not a transcription of it that can drift on its own.
    """
    source = CLIENT_TS.read_text(encoding="utf-8")
    match = re.search(rf"export interface {interface} \{{(.*?)\n\}}", source, re.S)
    assert match, f"no interface {interface} in client.ts; it was renamed or removed"

    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
    body = re.sub(r"//.*", "", body)
    return {name: bool(optional) for name, optional in re.findall(r"^\s*(\w+)(\??):", body, re.M)}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """A runtime over real skills and empty stores under `tmp_path`.

    Never the real `~/.tendon`: these endpoints read whatever is in the store, and a test
    that reads a developer's own episodes passes or fails according to what they did
    yesterday.
    """
    from tendon.api.app import create_app

    return TestClient(
        create_app(
            skill_root=REPO / "skills",
            episode_root=tmp_path / "episodes",
            memory_root=tmp_path / "memory",
            progress_root=tmp_path / "progress",
        )
    )


def _first(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        return payload[0] if payload else None
    return payload if isinstance(payload, dict) else None


def _assert_shape(sample: dict[str, Any], interface: str) -> None:
    declared = _declared(interface)
    missing = [name for name, optional in declared.items() if name not in sample and not optional]

    assert not missing, (
        f"{interface} declares {missing}, which the runtime does not send. The shell reads "
        f"them off a response that has no such keys, so the page shows nothing and the "
        f"disagreement is invisible from either side."
    )


@pytest.mark.parametrize(
    ("path", "interface"),
    [
        ("/api/health", "Health"),
        ("/api/bodies", "Body"),
        ("/api/skills", "SkillSummary"),
    ],
)
def test_what_the_runtime_sends_carries_what_the_shell_reads(
    client: TestClient, path: str, interface: str
) -> None:
    response = client.get(path)
    assert response.status_code == 200, response.text

    sample = _first(response.json())
    assert sample is not None, f"{path} returned nothing to check the shape of"
    _assert_shape(sample, interface)


def test_a_stored_memory_arrives_in_the_shape_the_shell_reads(
    client: TestClient, tmp_path: Path
) -> None:
    """Memory and progress are empty on a fresh store, and an empty list agrees with every
    interface ever written. Both are filled first so the check has something to check."""
    from tendon.kernel.types import (
        Action,
        ActionSpace,
        Confidence,
        ConfidenceSource,
        Intent,
        Observation,
        Proprioception,
    )
    from tendon.services.adaptive import CorrectionMemory
    from tendon.services.memory_store import save_memory

    memory = CorrectionMemory()
    memory.remember(
        Observation(step=0, proprio=Proprioception(joint_positions=[0.0] * 5)),
        Intent(
            actions=[Action(space=ActionSpace.JOINT_POSITION, values=[0.1] * 5)],
            horizon_s=0.1,
            # An operator's correction, so the source is the operator and not an estimator.
            confidence=Confidence(score=1.0, source=ConfidenceSource.NONE),
        ),
    )
    save_memory(tmp_path / "memory", "grasp/cube-sim", "mujoco:test", memory)

    sample = _first(client.get("/api/memory").json())
    assert sample is not None, "a saved memory did not come back"
    _assert_shape(sample, "Memory")


def test_a_recorded_episode_arrives_in_the_shape_the_progress_view_reads(
    client: TestClient, tmp_path: Path
) -> None:
    from tendon.services.progress import EpisodeRecord, append, now

    append(
        tmp_path / "progress",
        "grasp/cube-sim",
        "mujoco:test",
        EpisodeRecord(
            skill="grasp/cube-sim",
            body="mujoco:test",
            episode_id="abc",
            ended_at=now(),
            steps=120,
            interventions=1,
            corrections=1,
            corrections_known=1,
        ),
    )

    sample = _first(client.get("/api/progress").json())
    assert sample is not None, "an appended episode did not come back"
    _assert_shape(sample, "Progress")


def _session_snapshot() -> dict[str, Any]:
    """A snapshot without running an episode.

    The session endpoints need a live session, which is why this shape went unchecked while
    every other one was covered — and it is the shape the operator actually watches.
    Built from `SessionState` directly: the dictionary is what the contract is about, and
    driving MuJoCo to obtain one would test the scheduler instead.
    """
    from tendon.api.session import EpisodeSession, SessionState

    state = SessionState(session_id="s1", skill="grasp/cube-sim", body_id="mujoco:arm")
    state.stopped_because = "low_confidence interrupt at step 0 and no operator is attached"
    return EpisodeSession.snapshot(type("S", (), {"state": state})())


def test_the_session_shape_is_the_one_the_shell_declares() -> None:
    _assert_shape(_session_snapshot(), "SessionSnapshot")


def test_nothing_in_a_session_snapshot_is_dropped_by_the_shell() -> None:
    """Both directions here, unlike the endpoints above, because the shell is the only
    consumer of this one. A field the runtime puts in a session and the shell does not
    declare is not "extra data for another client" — it is information travelling to the
    operator's seat and being discarded on arrival.

    That is not hypothetical. `stopped_because` was sent from the day sessions were
    written and declared nowhere in the shell, so the one message that distinguishes "the
    policy raised its own hand and nobody answered" from "nothing happened" reached the
    view and was dropped.
    """
    declared = set(_declared("SessionSnapshot"))
    sent = set(_session_snapshot())

    dropped = sorted(sent - declared)
    assert not dropped, (
        f"the runtime sends {dropped} in a session snapshot and the shell reads none of "
        f"them. Declare the field in client.ts and show it, or stop sending it."
    )


def test_every_endpoint_the_shell_calls_is_one_the_runtime_serves(client: TestClient) -> None:
    """A path is a string on both sides. Renaming one in `app.py` breaks the shell and
    nothing else, which is the quietest way this pair can come apart."""
    source = CLIENT_TS.read_text(encoding="utf-8")

    # Template literals become the `{param}` FastAPI writes: `${id}` is a path parameter and
    # what it is called on each side is not the contract.
    called = {
        re.sub(r"\$\{[^}]+\}", "{}", path)
        for path in re.findall(r"[\"`](/api/[^\"`\s]*)[\"`]", source)
    }
    served = {
        re.sub(r"\{[^}]+\}", "{}", route.path)
        for route in client.app.routes
        if getattr(route, "path", "").startswith("/api/")
    }

    assert called, "no API paths found in client.ts; the pattern stopped matching"
    assert called <= served, f"the shell calls {sorted(called - served)}, which nothing serves"
