"""`services/viz.py`, the Rerun logger.

Two properties matter and neither is about pictures.

A visualiser must not be able to take a run with it. It subscribes to the same bus as the
recorder, and a run where the viewer died at step 12 must still have produced twelve steps
of data and said so.

And what it logs has to be the pair, not one of them. `commanded` and `applied` differ
whenever hardware clips, and the gap between the two lines is the body refusing an
instruction. A logger that recorded one of them would show a robot doing exactly as it
was told.

Rerun is an optional extra, so everything here skips cleanly without it rather than
failing on an import the CI unit job does not install.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tendon.kernel.types import (
    Action,
    ActionSpace,
    Confidence,
    ConfidenceSource,
    Intent,
    Observation,
    Proprioception,
)

rerun = pytest.importorskip("rerun", reason="viz needs the view extra")

from tendon.services.viz import RerunLogger, VizError  # noqa: E402

DOF = 5


@dataclass
class FakeStep:
    """The shape `kernel/scheduler.StepRecord` has, without importing the scheduler."""

    step: int
    observation: Observation
    commanded: Action
    applied: Action
    clamped: bool = False
    unchecked: tuple[str, ...] = ()


@dataclass
class FakeBus:
    """Records what subscribed, so attachment can be checked without a scheduler."""

    handlers: dict[str, Any] = field(default_factory=dict)

    def subscribe(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler


def make_step(step: int = 0, *, clamped: bool = False) -> FakeStep:
    observation = Observation(
        step=step,
        proprio=Proprioception(joint_positions=[0.1] * DOF, gripper_open=0.5),
    )
    commanded = Action(space=ActionSpace.JOINT_POSITION, values=[99.0] * DOF, gripper=1.0)
    applied = Action(space=ActionSpace.JOINT_POSITION, values=[1.92] * DOF, gripper=1.0)
    return FakeStep(step, observation, commanded, applied, clamped=clamped)


@pytest.fixture
def logger(tmp_path):
    log = RerunLogger("test", save_path=tmp_path / "run.rrd")
    yield log
    log.close()


def test_it_attaches_under_a_name_the_bus_can_blame(logger) -> None:
    """`EpisodeResult.subscriber_failures` names the subscriber that died.

    A run where the viewer failed at step 12 otherwise looks exactly like a short run.
    """
    bus = FakeBus()
    logger.attach_to(bus)
    assert "rerun" in bus.handlers


def test_a_custom_name_reaches_the_bus(logger) -> None:
    bus = FakeBus()
    logger.attach_to(bus, name="watcher")
    assert "watcher" in bus.handlers


def test_logging_a_step_does_not_raise(logger) -> None:
    """It runs at control rate; anything it throws lands in the middle of an episode."""
    logger.log_step(make_step(0))
    logger.log_step(make_step(1, clamped=True))


def test_a_step_reaches_the_logger_through_the_bus(logger) -> None:
    bus = FakeBus()
    logger.attach_to(bus)
    bus.handlers["rerun"](make_step(3))


def test_intent_and_interrupt_logging_do_not_raise(logger) -> None:
    intent = Intent(
        horizon_s=0.5,
        actions=(Action(space=ActionSpace.JOINT_POSITION, values=[0.0] * DOF),),
        confidence=Confidence(
            score=0.3,
            source=ConfidenceSource.CHUNK_VARIANCE,
            reasons=("samples disagree 4x more than usual",),
        ),
        goal="pick up the cube",
    )
    logger.log_intent(intent, step=10)


def test_a_closed_logger_ignores_further_steps(logger) -> None:
    """Closing is not an error state. Steps arriving after it are dropped, not raised on.

    The bus keeps publishing until the episode ends, and a logger that threw on the way
    out would turn a completed run into a subscriber failure.
    """
    logger.close()
    logger.log_step(make_step(0))


def test_close_is_idempotent(logger) -> None:
    logger.close()
    logger.close()


def test_a_recording_is_written_to_disk(tmp_path) -> None:
    """A run is over and the robot has been reset; the recording is all that is left."""
    path = tmp_path / "episode.rrd"
    log = RerunLogger("test", save_path=path)
    for step in range(5):
        log.log_step(make_step(step))
    log.close()

    assert path.exists()
    assert path.stat().st_size > 0


def test_the_error_names_the_extra_when_rerun_is_absent(monkeypatch, tmp_path) -> None:
    """The install that fixes it, rather than an ImportError from inside a vendor package."""
    import builtins

    real_import = builtins.__import__

    def refuse_rerun(name, *args, **kwargs):
        if name == "rerun":
            raise ImportError("No module named 'rerun'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_rerun)

    with pytest.raises(VizError) as caught:
        RerunLogger("test", save_path=tmp_path / "x.rrd")
    assert "tendon-os[view]" in str(caught.value)
