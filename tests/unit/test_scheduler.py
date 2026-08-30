"""The scheduler, tested against stub bodies and policies.

The invariant that matters most: every action reaching a driver has passed safety,
including one an operator supplied. There is one `driver.apply` call site in the module so
that is checkable by reading, and these tests check it by execution.
"""

from __future__ import annotations

import pytest

from tendon.kernel.bus import Bus
from tendon.kernel.interrupt import InterruptState
from tendon.kernel.scheduler import (
    EpisodeResult,
    Scheduler,
    StepRecord,
    UnsafeCorrection,
)
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Capability,
    Confidence,
    ConfidenceSource,
    GripperKind,
    Intent,
    InterruptContext,
    InterruptResolution,
    Observation,
    Proprioception,
    Resolution,
    SafetyLimits,
)


class FakeDriver:
    """A body that records what it was asked to do, and optionally clips.

    `clip_to` mimics an actuator range: real bodies execute an out-of-range command at
    the bound and report the bound back, which is the whole reason `apply` returns.
    """

    def __init__(self, *, clip_to: float | None = None, control_hz: float = 100.0) -> None:
        self._clip_to = clip_to
        self._control_hz = control_hz
        self.commanded: list[Action] = []
        self.closed = False
        self._step = 0

    @property
    def capability(self) -> Capability:
        return Capability(
            body_id="fake:arm",
            dof=2,
            gripper=GripperKind.PARALLEL,
            control_hz=self._control_hz,
            cameras=("wrist",),
        )

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        return (ActionSpace.JOINT_POSITION, ActionSpace.JOINT_VELOCITY)

    def reset(self, *, seed: int | None = None) -> Observation:
        self._step = 0
        self.commanded.clear()
        return self.observe()

    def observe(self) -> Observation:
        return Observation(
            step=self._step,
            proprio=Proprioception(joint_positions=[0.0, 0.0]),
        )

    def apply(self, action: Action) -> Action:
        self.commanded.append(action)
        self._step += 1
        if self._clip_to is None:
            return action
        return Action(
            space=action.space,
            values=[max(-self._clip_to, min(self._clip_to, v)) for v in action.values],
            gripper=action.gripper,
        )

    def close(self) -> None:
        self.closed = True


class FakePolicy:
    """Returns the same chunk every time, with a stated confidence."""

    def __init__(
        self,
        *,
        values: list[float] | None = None,
        steps: int = 2,
        score: float = 0.9,
        source: ConfidenceSource = ConfidenceSource.CHUNK_VARIANCE,
    ) -> None:
        self._values = values if values is not None else [0.01, 0.0]
        self._steps = steps
        self._score = score
        self._source = source
        self.resets = 0
        self.predictions = 0

    @property
    def name(self) -> str:
        return "fake/policy"

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        return (ActionSpace.JOINT_POSITION,)

    def reset(self) -> None:
        self.resets += 1

    def predict(self, observation: Observation) -> Intent:
        self.predictions += 1
        return Intent(
            horizon_s=0.5,
            actions=tuple(
                Action(space=ActionSpace.JOINT_POSITION, values=list(self._values))
                for _ in range(self._steps)
            ),
            confidence=Confidence(score=self._score, source=self._source),
            goal="move",
            target="cube",
        )


class StubHandler:
    """An operator who always answers the same way."""

    def __init__(
        self,
        resolution: Resolution = Resolution.APPROVED,
        correction: Intent | None = None,
    ) -> None:
        self._resolution = resolution
        self._correction = correction
        self.calls: list[InterruptContext] = []

    def resolve(self, context: InterruptContext) -> InterruptResolution:
        self.calls.append(context)
        return InterruptResolution(resolution=self._resolution, correction=self._correction)


def intent_of(values: list[float], steps: int = 1) -> Intent:
    return Intent(
        horizon_s=0.2,
        actions=tuple(
            Action(space=ActionSpace.JOINT_POSITION, values=list(values)) for _ in range(steps)
        ),
        confidence=Confidence(score=1.0, source=ConfidenceSource.CHUNK_VARIANCE),
    )


# ------------------------------------------------------------------------ basic running


def test_an_episode_runs_to_the_step_limit() -> None:
    driver = FakeDriver()
    result = Scheduler(driver=driver, limits=SafetyLimits()).run_episode(FakePolicy(), max_steps=10)
    assert result.steps == 10
    assert len(driver.commanded) == 10


def test_the_policy_is_reset_once_per_episode() -> None:
    policy = FakePolicy()
    Scheduler(driver=FakeDriver(), limits=SafetyLimits()).run_episode(policy, max_steps=4)
    assert policy.resets == 1


def test_every_episode_gets_its_own_id() -> None:
    scheduler = Scheduler(driver=FakeDriver(), limits=SafetyLimits())
    a = scheduler.run_episode(FakePolicy(), max_steps=2)
    b = scheduler.run_episode(FakePolicy(), max_steps=2)
    assert a.episode_id != b.episode_id


def test_deliberation_runs_less_often_than_control() -> None:
    """The two clocks. One prediction covers a whole chunk of control steps."""
    policy = FakePolicy(steps=5)
    result = Scheduler(driver=FakeDriver(), limits=SafetyLimits()).run_episode(policy, max_steps=10)
    assert result.steps == 10
    assert policy.predictions == 2


# ---------------------------------------------------------------------------- recording


def test_every_step_is_recorded_without_a_flag() -> None:
    """Design decision 1: the recorder is a subscriber, not a mode."""
    seen: list[StepRecord] = []
    bus: Bus[StepRecord] = Bus()
    bus.subscribe("test", seen.append)

    result = Scheduler(driver=FakeDriver(), limits=SafetyLimits(), bus=bus).run_episode(
        FakePolicy(), max_steps=6
    )

    assert len(seen) == 6
    assert len(result.records) == 6
    assert [r.step for r in seen] == list(range(6))


def test_a_failing_subscriber_does_not_stop_the_body() -> None:
    """A recorder that fills the disk is not a reason for a robot to stop mid-motion.

    The failure is isolated, reported on the result, and the episode runs to completion.
    """
    bus: Bus[StepRecord] = Bus()
    survived: list[StepRecord] = []

    def explode(record: StepRecord) -> None:
        raise OSError("no space left on device")

    bus.subscribe("broken-recorder", explode)
    bus.subscribe("shell-stream", survived.append)

    result = Scheduler(driver=FakeDriver(), limits=SafetyLimits(), bus=bus).run_episode(
        FakePolicy(), max_steps=5
    )

    assert result.steps == 5, "the episode must finish despite a dead subscriber"
    assert len(survived) == 5, "the surviving subscriber must still receive every step"
    assert len(result.subscriber_failures) == 1
    failure = result.subscriber_failures[0]
    assert failure.name == "broken-recorder"
    assert failure.step == 0
    assert "no space left" in failure.error


def test_commanded_and_applied_are_both_kept_when_the_body_clips() -> None:
    """Recording only the command would store what the policy asked for as the outcome.

    The policy would then train on its own requests as though they were results, and no
    test downstream would notice.
    """
    driver = FakeDriver(clip_to=0.005)
    result = Scheduler(driver=driver, limits=SafetyLimits()).run_episode(
        FakePolicy(values=[0.01, 0.0]), max_steps=1
    )

    record = result.records[0]
    assert record.commanded.values[0] == pytest.approx(0.01)
    assert record.applied.values[0] == pytest.approx(0.005)
    assert record.commanded.values != record.applied.values


def test_a_body_that_does_not_clip_reports_the_command_back() -> None:
    result = Scheduler(driver=FakeDriver(), limits=SafetyLimits()).run_episode(
        FakePolicy(values=[0.01, 0.0]), max_steps=1
    )
    record = result.records[0]
    assert record.commanded.values == record.applied.values


# ------------------------------------------------------------------------------- safety


class _RampPolicy(FakePolicy):
    """Commands a position that keeps climbing, so successive steps imply a velocity.

    A policy repeating one absolute position implies zero velocity no matter how large
    that position is, which is correct and makes it useless for testing a speed limit.
    """

    def __init__(self, *, rise: float = 0.5, steps: int = 4) -> None:
        super().__init__(steps=steps)
        self._rise = rise
        self._offset = 0.0

    def predict(self, observation: Observation) -> Intent:
        self.predictions += 1
        actions = tuple(
            Action(
                space=ActionSpace.JOINT_POSITION,
                values=[self._offset + i * self._rise, 0.0],
            )
            for i in range(self._steps)
        )
        self._offset += self._steps * self._rise
        return Intent(
            horizon_s=0.5,
            actions=actions,
            confidence=Confidence(score=0.9, source=ConfidenceSource.CHUNK_VARIANCE),
        )


def test_an_over_speed_action_is_clamped_before_reaching_the_driver() -> None:
    driver = FakeDriver(control_hz=100.0)
    # A 0.5 rad rise per step at 100Hz implies 50 rad/s, far over the 1.0 limit.
    result = Scheduler(driver=driver, limits=SafetyLimits(max_joint_velocity=1.0)).run_episode(
        _RampPolicy(rise=0.5), max_steps=6
    )

    # The first step has no previous action, so velocity is unmeasurable there and is
    # reported unchecked rather than passed silently. From the second onward it clamps.
    assert any("max_joint_velocity" in u for u in result.records[0].unchecked)
    assert any(r.clamped for r in result.records[1:]), "an over-speed step was not clamped"

    # Every step the driver saw after the first moves at most the reachable distance:
    # 1.0 rad/s over a 0.01 s period is 0.01 rad.
    deltas = [
        abs(b.values[0] - a.values[0])
        for a, b in zip(driver.commanded, driver.commanded[1:], strict=False)
    ]
    assert max(deltas) <= 0.01 + 1e-9


def test_unevaluable_limits_are_surfaced_not_swallowed() -> None:
    """A caller must be able to tell that the episode ran partly unverified."""
    result = Scheduler(
        driver=FakeDriver(),
        limits=SafetyLimits(workspace_min=[-0.1, -0.1, 0.0]),
    ).run_episode(FakePolicy(), max_steps=3)

    assert any("workspace" in u for u in result.unchecked)


def test_a_workspace_breach_cannot_be_clamped_so_it_hands_over() -> None:
    handler = StubHandler(Resolution.ABORTED)
    driver = FakeDriver()
    result = Scheduler(
        driver=driver,
        limits=SafetyLimits(workspace_min=[0.0, 0.0, 0.0], workspace_max=[0.1, 0.1, 0.1]),
        handler=handler,
    ).run_episode(_EePolicy(), max_steps=5)

    assert handler.calls, "a breach that cannot be clamped must hand over"
    assert handler.calls[0].reason.value == "safety_trip"
    assert result.state is InterruptState.STOPPED


class _EePolicy(FakePolicy):
    """Emits an absolute end-effector pose outside any sane workspace."""

    def predict(self, observation: Observation) -> Intent:
        self.predictions += 1
        return Intent(
            horizon_s=0.2,
            actions=(Action(space=ActionSpace.EE_ABS_POSE, values=[9.0, 0.0, 0.0, 0, 0, 0]),),
            confidence=Confidence(score=1.0, source=ConfidenceSource.CHUNK_VARIANCE),
        )


# ---------------------------------------------------------------------------- handover


def test_low_confidence_hands_over() -> None:
    handler = StubHandler(Resolution.APPROVED)
    Scheduler(driver=FakeDriver(), limits=SafetyLimits(), handler=handler).run_episode(
        FakePolicy(score=0.1), max_steps=2
    )

    assert handler.calls
    assert handler.calls[0].reason.value == "low_confidence"


def test_an_unmeasured_confidence_never_hands_over() -> None:
    """ADR 0003. A score with no source is not a measurement, so it cannot trigger."""
    handler = StubHandler()
    result = Scheduler(driver=FakeDriver(), limits=SafetyLimits(), handler=handler).run_episode(
        FakePolicy(score=0.0, source=ConfidenceSource.NONE), max_steps=4
    )

    assert handler.calls == []
    assert result.steps == 4


def test_with_no_handler_a_handover_stops_the_episode() -> None:
    """Nobody to ask. Continuing would execute an action judged unfit to run alone."""
    result = Scheduler(driver=FakeDriver(), limits=SafetyLimits()).run_episode(
        FakePolicy(score=0.1), max_steps=10
    )
    assert result.steps == 0
    assert result.interventions == 0


def test_approval_resumes_and_is_counted() -> None:
    result = Scheduler(
        driver=FakeDriver(),
        limits=SafetyLimits(),
        handler=StubHandler(Resolution.APPROVED),
    ).run_episode(FakePolicy(score=0.1, steps=2), max_steps=4)

    assert result.steps == 4
    assert result.interventions >= 1
    assert result.corrections == 0


def test_abort_ends_the_episode_as_stopped_not_faulted() -> None:
    result = Scheduler(
        driver=FakeDriver(),
        limits=SafetyLimits(),
        handler=StubHandler(Resolution.ABORTED),
    ).run_episode(FakePolicy(score=0.1), max_steps=10)

    assert result.state is InterruptState.STOPPED
    assert result.fault_reason == ()


# -------------------------------------------------------------------------- corrections


def test_a_correction_is_executed_and_counted() -> None:
    driver = FakeDriver()
    correction = intent_of([0.02, 0.0], steps=2)
    result = Scheduler(
        driver=driver,
        limits=SafetyLimits(),
        handler=StubHandler(Resolution.CORRECTED, correction=correction),
    ).run_episode(FakePolicy(score=0.1, values=[0.01, 0.0]), max_steps=2)

    assert result.corrections >= 1
    assert driver.commanded[0].values[0] == pytest.approx(0.02)


def test_a_correction_that_breaches_a_limit_is_refused_loudly() -> None:
    """An operator may correct a policy but may not exceed a hard limit.

    Raised rather than dropped: silently discarding it would leave the operator believing
    their correction was applied.
    """
    correction = Intent(
        horizon_s=0.2,
        actions=(Action(space=ActionSpace.EE_ABS_POSE, values=[9.0, 0, 0, 0, 0, 0]),),
        confidence=Confidence(score=1.0, source=ConfidenceSource.CHUNK_VARIANCE),
    )
    scheduler = Scheduler(
        driver=FakeDriver(),
        limits=SafetyLimits(workspace_max=[0.1, 0.1, 0.1]),
        handler=StubHandler(Resolution.CORRECTED, correction=correction),
    )

    with pytest.raises(UnsafeCorrection):
        scheduler.run_episode(FakePolicy(score=0.1), max_steps=4)


def test_rejection_without_an_alternative_stops() -> None:
    """Asking the policy for alternatives is v0.2 shell work. Until then, stopping is
    the honest behaviour rather than running the plan that was just declined."""
    driver = FakeDriver()
    result = Scheduler(
        driver=driver,
        limits=SafetyLimits(),
        handler=StubHandler(Resolution.REJECTED),
    ).run_episode(FakePolicy(score=0.1), max_steps=10)

    assert result.steps == 0
    assert driver.commanded == []


# -------------------------------------------------------------------------------- faults


def test_max_steps_is_respected_across_chunk_boundaries() -> None:
    """A chunk longer than the remaining budget must not overrun it."""
    result = Scheduler(driver=FakeDriver(), limits=SafetyLimits()).run_episode(
        FakePolicy(steps=7), max_steps=10
    )
    assert result.steps == 10


def test_result_reports_terminal_state() -> None:
    result: EpisodeResult = Scheduler(driver=FakeDriver(), limits=SafetyLimits()).run_episode(
        FakePolicy(), max_steps=3
    )
    assert result.state is InterruptState.RUNNING
