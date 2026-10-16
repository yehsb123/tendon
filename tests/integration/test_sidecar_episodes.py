"""One row per episode in the sidecar, carrying the evaluator's verdict.

ADR 0007's estimator indexes successful episodes, and until this table existed nothing on
disk said which episodes succeeded: `Recorder.finish(success=...)` put the verdict on the
returned `EpisodeMeta` and nowhere else. The progress log has it, keyed by the scheduler's
id, which joins to neither the sidecar nor the parquet.

Integration rather than unit for the reason `test_sidecar_join.py` gives: the claim is
that a duckdb file and a LeRobot parquet agree, and a mock of either agrees with itself.
"""

from __future__ import annotations

import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("lerobot", reason="writes a real LeRobotDataset; needs the robot extra")

from tendon.kernel.types import (  # noqa: E402
    Action,
    ActionSpace,
    Capability,
    Confidence,
    ConfidenceSource,
    GripperKind,
    Intent,
    InterruptContext,
    InterruptReason,
    InterruptResolution,
    Observation,
    Proprioception,
    Resolution,
)
from tendon.services.recorder import Recorder  # noqa: E402

DOF = 5
FRAMES = 4


def capability() -> Capability:
    return Capability(body_id="probe", dof=DOF, control_hz=100.0, gripper=GripperKind.PARALLEL)


def observation(step: int) -> Observation:
    return Observation(
        step=step, proprio=Proprioception(joint_positions=[0.0] * DOF, gripper_open=0.5)
    )


def interrupt(recorder: Recorder, step: int) -> None:
    intent = Intent(
        horizon_s=0.1,
        actions=(Action(space=ActionSpace.JOINT_POSITION, values=[0.0] * DOF),),
        confidence=Confidence(
            score=0.2, source=ConfidenceSource.CHUNK_VARIANCE, reasons=("samples disagree",)
        ),
    )
    recorder.note_interrupt(
        InterruptContext(
            episode_id="probe",
            step=step,
            reason=InterruptReason.LOW_CONFIDENCE,
            intent=intent,
            observation=observation(step),
        ),
        InterruptResolution(resolution=Resolution.CORRECTED, note="from the left"),
    )


def record_episode(
    recorder: Recorder,
    *,
    success: bool | None,
    frames: int = FRAMES,
    interrupts: int = 0,
) -> str:
    episode_id = recorder.start("probe/verdict", capability())
    for step in range(frames):
        recorder.record(
            observation(step),
            Action(space=ActionSpace.JOINT_POSITION, values=[0.1] * DOF, gripper=1.0),
        )
    for step in range(interrupts):
        interrupt(recorder, step)
    recorder.finish(success=success)
    return episode_id


@pytest.fixture
def recorder(tmp_path: pathlib.Path) -> Recorder:
    return Recorder(root=tmp_path, repo_id="probe/verdict", use_videos=False)


def episode_rows(recorder: Recorder) -> list[tuple]:
    con = duckdb.connect(str(recorder.sidecar_path), read_only=True)
    try:
        return con.execute(
            "SELECT episode_id, episode_index, skill, success, interrupts FROM episodes "
            "ORDER BY episode_index NULLS LAST, episode_id"
        ).fetchall()
    finally:
        con.close()


def test_each_episode_has_a_row_with_its_verdict(recorder: Recorder) -> None:
    """True, False and not judged are three different rows, not two."""
    first = record_episode(recorder, success=True)
    second = record_episode(recorder, success=False)
    third = record_episode(recorder, success=None)

    assert episode_rows(recorder) == [
        (first, 0, "probe/verdict", True, 0),
        (second, 1, "probe/verdict", False, 0),
        # NULL, not False: the rig could not judge, which is not a failure.
        (third, 2, "probe/verdict", None, 0),
    ]


def test_the_row_counts_the_interrupts_written_beside_it(recorder: Recorder) -> None:
    record_episode(recorder, success=True)
    corrected = record_episode(recorder, success=True, interrupts=2)

    rows = {row[0]: row for row in episode_rows(recorder)}
    assert rows[corrected][4] == 2

    con = duckdb.connect(str(recorder.sidecar_path), read_only=True)
    try:
        written = con.execute(
            "SELECT count(*) FROM interrupts WHERE episode_id = ?", [corrected]
        ).fetchone()
    finally:
        con.close()
    assert written == (2,)


def test_a_discarded_episode_with_interrupts_keeps_its_index_null(recorder: Recorder) -> None:
    """Zero frames are discarded from the parquet; the interrupt row is still written.

    The episodes row follows the interrupts row, so the verdict for what the operator did
    is not lost, and its index stays NULL because there is no parquet row to join to.
    """
    kept = record_episode(recorder, success=True)
    discarded = record_episode(recorder, success=False, frames=0, interrupts=1)

    assert episode_rows(recorder) == [
        (kept, 0, "probe/verdict", True, 0),
        (discarded, None, "probe/verdict", False, 1),
    ]


def test_an_episode_that_wrote_nothing_writes_no_row(recorder: Recorder) -> None:
    """No frames and no interrupts leave no frames or interrupts rows; no episodes row either.

    A row here would point at nothing in either the parquet or the other two tables.
    """
    kept = record_episode(recorder, success=True)
    record_episode(recorder, success=None, frames=0)

    assert episode_rows(recorder) == [(kept, 0, "probe/verdict", True, 0)]


def test_a_sidecar_from_before_the_table_gets_no_invented_rows(recorder: Recorder) -> None:
    """Older episodes had no verdict written; the table appears and they stay absent."""
    older = record_episode(recorder, success=True)

    con = duckdb.connect(str(recorder.sidecar_path))
    try:
        con.execute("DROP TABLE episodes")
    finally:
        con.close()

    newer = record_episode(recorder, success=False)

    rows = episode_rows(recorder)
    assert [row[0] for row in rows] == [newer]
    assert older not in {row[0] for row in rows}


def test_the_verdict_and_the_frames_are_one_transaction(recorder: Recorder) -> None:
    """If the episodes row cannot be written, the frames row is not written either.

    A frame without a verdict reads as an episode nobody judged, which is a claim the
    store would be making on nobody's behalf.
    """
    record_episode(recorder, success=True)

    con = duckdb.connect(str(recorder.sidecar_path))
    try:
        # A table that refuses the next row: NOT NULL on a verdict that will be None.
        con.execute("DROP TABLE episodes")
        con.execute(
            "CREATE TABLE episodes (episode_id VARCHAR, episode_index BIGINT, skill VARCHAR, "
            "success BOOLEAN NOT NULL, interrupts BIGINT, ended_at TIMESTAMPTZ)"
        )
        before = con.execute("SELECT count(*) FROM frames").fetchone()
    finally:
        con.close()

    with pytest.raises(duckdb.Error):
        record_episode(recorder, success=None)

    con = duckdb.connect(str(recorder.sidecar_path), read_only=True)
    try:
        after = con.execute("SELECT count(*) FROM frames").fetchone()
    finally:
        con.close()
    assert after == before


class _CommitFails:
    """A connection whose COMMIT ends the transaction and then reports a disk error.

    That is what a failed write at commit time leaves behind: no transaction is open, so a
    ROLLBACK afterwards raises one of its own.
    """

    def __init__(self, con: object) -> None:
        self._con = con

    def execute(self, sql: str, *args: object) -> object:
        result = self._con.execute(sql, *args)  # type: ignore[attr-defined]
        if sql.strip().upper() == "COMMIT":
            raise OSError("disk full while committing the sidecar")
        return result

    def __getattr__(self, name: str) -> object:
        return getattr(self._con, name)


def test_a_failed_commit_reports_its_own_error(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller is told the disk failed, not that a rollback found nothing to roll back."""
    real_connect = duckdb.connect
    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: _CommitFails(real_connect(*a, **k)))

    with pytest.raises(OSError, match="disk full"):
        record_episode(recorder, success=True)
