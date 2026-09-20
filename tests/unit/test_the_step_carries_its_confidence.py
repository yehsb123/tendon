"""The half of threshold calibration that was never written down.

ADR 0003's postscript splits calibration in two. The **scale** — how much disagreement is
typical — is measured and shipped: `tendon calibrate`, 0.0777 over 26 predictions. The
**threshold** — how much disagreement means *ask* — needs episodes where somebody took
over and what happened after, and that has been the standing answer for why it is not done.

It is not the whole answer. Working out a threshold from outcomes means asking, for each
candidate value, which steps *would* have been handed over and whether those episodes then
succeeded. That needs the score at each step. `services/recorder.py` has had a `confidence`
column since the sidecar was written and its own docstring calls the gap what it is:

    Confidence is not recorded here and that is a gap, not a decision. `StepRecord`
    carries no confidence ... Until the scheduler carries it, the sidecar's confidence
    column is null for bus-driven episodes.

Every real episode is bus-driven. Measured on the episodes this machine has recorded:
**1,900 frames, 0 with a confidence value.** So the data was never being collected, and no
number of further runs would have produced it.

The scheduler carries it now. Reading it into the sidecar is `services/recorder.py`, which
is Track A's file — noted in Status rather than taken.
"""

from __future__ import annotations

import pytest

from tendon.kernel.bus import Bus
from tendon.kernel.scheduler import Scheduler, StepRecord
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Capability,
    Confidence,
    ConfidenceSource,
    GripperKind,
    Intent,
    Observation,
    Proprioception,
    SafetyLimits,
)

JOINTS = ("j1", "j2")


class Body:
    """Two joints, no clipping, no opinions."""

    def __init__(self) -> None:
        self.step = 0

    @property
    def capability(self) -> Capability:
        return Capability(
            body_id="stub:arm",
            dof=len(JOINTS),
            joint_names=JOINTS,
            gripper=GripperKind.NONE,
            control_hz=50.0,
        )

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        return (ActionSpace.JOINT_POSITION,)

    def _observation(self) -> Observation:
        return Observation(
            step=self.step,
            proprio=Proprioception(joint_positions=(0.0,) * len(JOINTS)),
        )

    def reset(self, *, seed: int | None = None) -> Observation:
        self.step = 0
        return self._observation()

    def observe(self) -> Observation:
        return self._observation()

    def apply(self, action: Action) -> Action:
        self.step += 1
        return action

    def close(self) -> None:
        pass


class Constant:
    """A policy that reports the confidence it is told to, on every chunk."""

    def __init__(self, confidence: Confidence, *, chunk: int = 3) -> None:
        self._confidence = confidence
        self._chunk = chunk

    @property
    def name(self) -> str:
        return "constant"

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        return (ActionSpace.JOINT_POSITION,)

    def reset(self) -> None:
        pass

    def predict(self, observation: Observation) -> Intent:
        return Intent(
            actions=tuple(
                Action(space=ActionSpace.JOINT_POSITION, values=[0.0] * len(JOINTS))
                for _ in range(self._chunk)
            ),
            horizon_s=self._chunk / 50.0,
            confidence=self._confidence,
        )


def _run(confidence: Confidence, *, steps: int = 6) -> list[StepRecord]:
    scheduler = Scheduler(
        driver=Body(),
        limits=SafetyLimits(),
        # Below every score used here, so nothing hands over and the records are the
        # policy's own. Handover is a separate test.
        confidence_threshold=0.0,
    )
    result = scheduler.run_episode(Constant(confidence), max_steps=steps)
    return list(result.records)


def test_every_step_carries_the_score_of_the_chunk_it_came_from() -> None:
    """One `predict` yields many actions, so the score repeats across the chunk. Repeating
    it is the point: the recorder subscribes to steps, and that is the only boundary it
    sees."""
    measured = Confidence(score=0.42, source=ConfidenceSource.CHUNK_VARIANCE)

    records = _run(measured)

    assert len(records) == 6
    assert [r.measured_confidence for r in records] == [0.42] * 6


def test_an_unmeasured_score_is_not_handed_out_as_a_number() -> None:
    """`ConfidenceSource.NONE` means no estimator ran, so `score` is a default and not an
    observation. A consumer that stored the bare float would write a default into a column
    a calibration later reads as data."""
    records = _run(Confidence(score=1.0, source=ConfidenceSource.NONE))

    assert records, "the run produced no steps, so nothing below was checked"
    assert all(r.measured_confidence is None for r in records)
    assert all(r.confidence is not None for r in records), (
        "the Confidence itself is still carried - the source is what makes the score unusable"
    )


def test_a_deterministic_policy_reporting_perfect_certainty_is_still_none() -> None:
    """The case this guard exists for, and it is not hypothetical.

    ACT is deterministic: three samples of one observation give one chunk, the spread is
    zero, and zero spread reads as certainty 1.0000. A policy that can never raise its own
    hand, wearing the number that says it never needs to. The adapter reports `NONE` for
    it, and this is what must survive the trip to storage.
    """
    act_like = Confidence(score=1.0, source=ConfidenceSource.NONE, reasons=("samples identical",))

    records = _run(act_like)

    assert all(r.measured_confidence is None for r in records)
    assert records[0].confidence is not None
    assert records[0].confidence.score == 1.0, "the raw score is still readable, just not as data"


def test_the_score_reaches_a_bus_subscriber() -> None:
    """The recorder is a bus subscriber and nothing else crosses that boundary. A field on
    `StepRecord` that the bus did not carry would be a field nothing can read."""
    seen: list[float | None] = []
    bus = Bus()
    bus.subscribe("recorder", lambda record: seen.append(record.measured_confidence))

    scheduler = Scheduler(
        driver=Body(),
        limits=SafetyLimits(),
        confidence_threshold=0.0,
        bus=bus,
    )
    scheduler.run_episode(
        Constant(Confidence(score=0.3, source=ConfidenceSource.CHUNK_VARIANCE)), max_steps=4
    )

    assert seen == [0.3] * 4


@pytest.mark.parametrize(
    "source",
    [ConfidenceSource.CHUNK_VARIANCE, ConfidenceSource.ENSEMBLE, ConfidenceSource.LEARNED_HEAD],
)
def test_any_real_estimator_survives_the_trip(source: ConfidenceSource) -> None:
    """Parametrised over the sources that mean something measured it, so adding an
    estimator does not quietly land in the `NONE` branch."""
    records = _run(Confidence(score=0.61, source=source))

    assert [r.measured_confidence for r in records] == [0.61] * len(records)
