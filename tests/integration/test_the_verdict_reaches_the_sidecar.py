"""The verdict in the sidecar is the verdict in the progress log.

Both come from `judge_result`. Until the callers judged before closing the recorder, the
progress log had a verdict and the sidecar had none: `tendon run`, `tendon eval` and the
API all called `finish()` with nothing and judged afterwards, so ADR 0007's index could
not be built from the store. These run the real commands and compare the two files,
because the claim is that two writers agree and a test of either alone would not show it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.cli.main import app  # noqa: E402

RUNNER = CliRunner()
REPO = Path(__file__).resolve().parents[2]
SKILL = "grasp/cube-sim"
SIDECAR = Path("grasp__cube-sim") / "tendon_sidecar.duckdb"


def progress_verdicts(progress_root: Path) -> list[bool | None]:
    return [
        json.loads(line)["succeeded"]
        for path in sorted(progress_root.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sidecar_verdicts(store: Path) -> list[bool | None]:
    con = duckdb.connect(str(store / SIDECAR), read_only=True)
    try:
        rows = con.execute("SELECT success FROM episodes ORDER BY episode_index").fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def test_a_run_writes_the_same_verdict_to_both(tmp_path: Path) -> None:
    store = tmp_path / "episodes"
    result = RUNNER.invoke(app, ["run", SKILL, "--steps", "60", "--store", str(store)])
    assert result.exit_code == 0, result.output

    logged = progress_verdicts(tmp_path / "progress")
    # The cube-sim body reports `cube_height`, so this episode is judged. A None here
    # would make the comparison below pass on two absences.
    assert len(logged) == 1 and logged[0] is not None
    assert sidecar_verdicts(store) == logged


def test_an_evaluation_writes_each_episodes_verdict_to_both(tmp_path: Path) -> None:
    store = tmp_path / "episodes"
    result = RUNNER.invoke(
        app,
        ["eval", SKILL, "--episodes", "2", "--steps", "30", "--store", str(store)],
    )
    assert result.exit_code == 0, result.output

    logged = progress_verdicts(tmp_path / "progress")
    assert len(logged) == 2 and None not in logged
    assert sidecar_verdicts(store) == logged


def test_a_run_that_raises_still_closes_the_recorder_unjudged(tmp_path: Path, monkeypatch) -> None:
    """The `finally` that closes the dataset is kept: a run that raises has no result to
    judge, so the recorder is closed with None rather than not closed."""
    from tendon.kernel.scheduler import Scheduler
    from tendon.services.recorder import Recorder

    closed: list[bool | None] = []
    real_finish = Recorder.finish

    def spy(self, success=None):
        closed.append(success)
        return real_finish(self, success=success)

    def explode(self, policy, *, max_steps, seed=None):
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(Recorder, "finish", spy)
    monkeypatch.setattr(Scheduler, "run_episode", explode)

    result = RUNNER.invoke(app, ["run", SKILL, "--steps", "5", "--store", str(tmp_path / "e")])

    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert closed == [None]


def test_an_episode_from_the_shell_writes_the_same_verdict_to_both(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from tendon.api.app import create_app

    store = tmp_path / "episodes"
    progress_root = tmp_path / "progress"
    client = TestClient(
        create_app(
            skill_root=REPO / "skills",
            episode_root=store,
            memory_root=tmp_path / "memory",
            progress_root=progress_root,
        )
    )
    response = client.post(
        "/api/sessions", json={"skill": SKILL, "body": "mujoco", "max_steps": 40}
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/ws/{session_id}") as socket:
        deadline = time.time() + 120
        while time.time() < deadline:
            message = socket.receive_json()
            if message.get("type") == "interrupt":
                client.post(f"/api/sessions/{session_id}/decide", json={"resolution": "approved"})
            if message.get("type") == "finished":
                break

    logged = progress_verdicts(progress_root)
    assert len(logged) == 1 and logged[0] is not None
    assert sidecar_verdicts(store) == logged
