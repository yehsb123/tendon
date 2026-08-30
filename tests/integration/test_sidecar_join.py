"""The column that lets the sidecar be joined to the dataset.

`services/progress.py` opens by explaining why it keeps its own append-only log rather
than deriving one from the store: the sidecar keyed interrupts by the recorder's uuid
while the parquet numbers episodes from zero, and nothing joined the two. "How many
interrupts did episode 7 have" could not be answered from recorded data at all.

These tests hold the join down. Integration rather than unit because the claim is about
two files agreeing -- a duckdb sidecar and a LeRobot parquet -- and a test that mocked
either one would be asserting that the mock agrees with itself.
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


def record_episode(recorder: Recorder, *, interrupt_at: int | None = None) -> None:
    recorder.start("probe/join", capability())
    observation = None
    for step in range(FRAMES):
        observation = Observation(
            step=step,
            proprio=Proprioception(joint_positions=[0.0] * DOF, gripper_open=0.5),
        )
        recorder.record(
            observation,
            Action(space=ActionSpace.JOINT_POSITION, values=[0.1] * DOF, gripper=1.0),
        )
    if interrupt_at is not None:
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
                step=interrupt_at,
                reason=InterruptReason.LOW_CONFIDENCE,
                intent=intent,
                observation=observation,
            ),
            InterruptResolution(resolution=Resolution.CORRECTED, note="approach from the left"),
        )
    recorder.finish()


@pytest.fixture
def recorder(tmp_path: pathlib.Path) -> Recorder:
    return Recorder(root=tmp_path, repo_id="probe/join", use_videos=False)


def parquet_episode_indices(root: pathlib.Path) -> list[int]:
    """Read the indices LeRobot actually assigned.

    Only `data/`: `meta/tasks.parquet` sits under the same tree with a different schema,
    and a glob that catches it fails on the mismatch rather than on anything meaningful.
    """
    files = sorted(str(p) for p in (root / "probe__join" / "data").rglob("*.parquet"))
    # An explicit connection, closed, rather than `duckdb.sql`. That helper runs on a
    # module-level default connection which nothing ever closes, so its native thread pool
    # is still live at interpreter shutdown and tears down in whatever order it gets --
    # which is what "terminate called without an active exception" means when a suite
    # reports every test passed and then exits 134. Every other duckdb user in this
    # repository already pairs connect with close; this was the one that did not.
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT episode_index FROM read_parquet({files!r}) ORDER BY 1"
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def test_frames_carry_the_index_lerobot_assigned(recorder: Recorder, tmp_path) -> None:
    """The sidecar's idea of which episode this was has to be the dataset's idea."""
    for _ in range(3):
        record_episode(recorder)

    con = duckdb.connect(str(recorder.sidecar_path))
    try:
        rows = con.execute(
            "SELECT episode_index, count(*) FROM frames GROUP BY 1 ORDER BY 1"
        ).fetchall()
    finally:
        con.close()

    assert rows == [(0, FRAMES), (1, FRAMES), (2, FRAMES)]
    assert parquet_episode_indices(tmp_path) == [0, 1, 2]


def test_an_interrupt_can_be_attributed_to_an_episode(recorder: Recorder) -> None:
    """The question progress.py could not ask the store: which episode was corrected."""
    record_episode(recorder)
    record_episode(recorder, interrupt_at=2)
    record_episode(recorder)

    con = duckdb.connect(str(recorder.sidecar_path))
    try:
        rows = con.execute("SELECT episode_index, reason FROM interrupts").fetchall()
    finally:
        con.close()

    assert rows == [(1, "low_confidence")]


def test_a_sidecar_written_before_the_column_still_accepts_rows(recorder: Recorder) -> None:
    """The tables already existed on disk in the field, so the ALTER has to run.

    The old rows stay NULL. The join they needed was never recorded, and inventing one
    would be worse than admitting the gap.
    """
    record_episode(recorder)

    con = duckdb.connect(str(recorder.sidecar_path))
    try:
        con.execute("DROP TABLE frames")
        con.execute(
            "CREATE TABLE frames (episode_id VARCHAR, frame_index BIGINT, confidence DOUBLE,"
            " intervention BOOLEAN, sim_time_s DOUBLE)"
        )
        con.execute("INSERT INTO frames VALUES ('older', 0, 0.5, false, 0.0)")
    finally:
        con.close()

    record_episode(recorder)

    con = duckdb.connect(str(recorder.sidecar_path))
    try:
        rows = con.execute(
            "SELECT episode_id, episode_index FROM frames GROUP BY 1, 2 ORDER BY 2 NULLS FIRST"
        ).fetchall()
    finally:
        con.close()

    assert rows[0] == ("older", None), "a row from before the column should not be invented"
    assert rows[-1][1] == 1, "the new episode should carry the index LeRobot gave it"
