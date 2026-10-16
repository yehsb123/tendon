"""The demonstration index, built from what the recorder wrote.

ADR 0007 shipped an estimator that only a caller who already knew which episodes
succeeded could build. The verdict now reaches the sidecar's `episodes` table, and these
tests hold the reader that joins it to the parquet: a real LeRobotDataset written by the
real recorder, read back with duckdb and no LeRobot on the read side.
"""

from __future__ import annotations

import math
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
from tendon.services.demonstration import (  # noqa: E402
    estimate_from_demonstrations,
    index_from_store,
    read_demonstrations,
)
from tendon.services.recorder import Recorder  # noqa: E402

DOF = 3
FRAMES = 12
HORIZON = 3
REPO_ID = "probe/demo"


def capability() -> Capability:
    return Capability(body_id="probe", dof=DOF, control_hz=100.0, gripper=GripperKind.PARALLEL)


def state_at(offset: float, step: int) -> list[float]:
    return [offset + 0.01 * step + 0.001 * joint for joint in range(DOF)]


def record(
    recorder: Recorder,
    offset: float,
    *,
    success: bool | None,
    interrupted: bool = False,
) -> None:
    recorder.start(REPO_ID, capability())
    for step in range(FRAMES):
        here = state_at(offset, step)
        recorder.record(
            Observation(step=step, proprio=Proprioception(joint_positions=here, gripper_open=0.5)),
            Action(
                space=ActionSpace.JOINT_POSITION,
                values=[value + 0.005 for value in here],
                gripper=0.25,
            ),
        )
    if interrupted:
        observation = Observation(
            step=2, proprio=Proprioception(joint_positions=state_at(offset, 2), gripper_open=0.5)
        )
        recorder.note_interrupt(
            InterruptContext(
                episode_id="probe",
                step=2,
                reason=InterruptReason.LOW_CONFIDENCE,
                intent=Intent(
                    horizon_s=0.1,
                    actions=(Action(space=ActionSpace.JOINT_POSITION, values=[0.0] * DOF),),
                    confidence=Confidence(score=0.2, source=ConfidenceSource.CHUNK_VARIANCE),
                ),
                observation=observation,
            ),
            InterruptResolution(resolution=Resolution.APPROVED),
        )
    recorder.finish(success=success)


@pytest.fixture
def recorder(tmp_path: pathlib.Path) -> Recorder:
    return Recorder(root=tmp_path, repo_id=REPO_ID, use_videos=False)


def directory(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / REPO_ID.replace("/", "__")


def test_each_stored_episode_comes_back_with_its_verdict(
    recorder: Recorder, tmp_path: pathlib.Path
) -> None:
    record(recorder, 0.0, success=True)
    record(recorder, 0.1, success=False)
    record(recorder, 0.2, success=True, interrupted=True)
    record(recorder, 0.3, success=None)

    demos = read_demonstrations(directory(tmp_path))

    assert [(d.episode_id, d.succeeded, d.interventions) for d in demos] == [
        ("0", True, 0),
        ("1", False, 0),
        ("2", True, 1),
        ("3", None, 0),
    ]


def test_states_and_actions_are_the_recorded_ones_without_the_jaw(
    recorder: Recorder, tmp_path: pathlib.Path
) -> None:
    """`action` on disk carries a trailing gripper channel; the index compares joints."""
    record(recorder, 0.5, success=True)

    (demo,) = read_demonstrations(directory(tmp_path))

    assert len(demo.states) == FRAMES and len(demo.actions) == FRAMES
    assert demo.states[4] == pytest.approx(state_at(0.5, 4), abs=1e-6)
    assert demo.actions[4].values == pytest.approx([v + 0.005 for v in state_at(0.5, 4)], abs=1e-6)
    assert demo.actions[4].gripper == pytest.approx(0.25)


def test_episodes_from_before_the_table_are_unjudged_not_failed(
    recorder: Recorder, tmp_path: pathlib.Path
) -> None:
    """No verdict was written for them, so the index leaves them out and says why."""
    record(recorder, 0.0, success=True)

    con = duckdb.connect(str(recorder.sidecar_path))
    try:
        con.execute("DROP TABLE episodes")
    finally:
        con.close()

    (demo,) = read_demonstrations(directory(tmp_path))
    assert demo.succeeded is None and demo.interventions is None

    index = index_from_store(directory(tmp_path), horizon=HORIZON)
    assert index.windows == 0
    assert index.excluded == (("0", "success was not measured"),)


def test_an_index_from_the_store_scores_a_chunk(recorder: Recorder, tmp_path) -> None:
    """Only the two unaided successes are indexed; the scale comes from them."""
    record(recorder, 0.0, success=True)
    record(recorder, 0.0537, success=True)
    record(recorder, 1.0, success=False)
    record(recorder, 2.0, success=True, interrupted=True)

    index = index_from_store(directory(tmp_path), horizon=HORIZON)

    assert index.episodes == ("0", "1")
    assert dict(index.excluded) == {"2": "did not succeed", "3": "an operator took over 1 time(s)"}
    assert index.reference_distance is not None and index.reference_distance > 0

    here = state_at(0.0, 3)
    chunk = [
        Action(space=ActionSpace.JOINT_POSITION, values=[v + 0.005 for v in state_at(0.0, s)])
        for s in range(3, 3 + HORIZON)
    ]
    confidence = estimate_from_demonstrations(index, here, chunk)

    assert confidence.source is ConfidenceSource.DEMONSTRATION
    assert math.isfinite(confidence.score) and confidence.score > 0.5


def test_a_store_with_no_sidecar_indexes_nothing(recorder: Recorder, tmp_path) -> None:
    record(recorder, 0.0, success=True)
    recorder.sidecar_path.unlink()

    index = index_from_store(directory(tmp_path), horizon=HORIZON)

    assert index.windows == 0
    assert index.reference_distance is None
