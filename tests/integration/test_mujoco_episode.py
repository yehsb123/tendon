"""The kernel against a real body.

Unit tests use stub drivers, which means every one of them agrees with the kernel about
what a driver does. This file is where that assumption gets checked against MuJoCo, and
it is the only place a mismatch between the `Driver` contract and an implementation of it
can surface.

CPU only, no hardware, no GPU. Skipped when the sim extra is not installed, which is the
one case where skipping is right: the test cannot run rather than choosing not to.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")

from tendon.drivers import base as driver_base  # noqa: E402
from tendon.kernel.bus import Bus  # noqa: E402
from tendon.kernel.protocols import Driver, Policy  # noqa: E402
from tendon.kernel.scheduler import Scheduler, StepRecord  # noqa: E402
from tendon.kernel.types import ActionSpace, SafetyLimits  # noqa: E402
from tendon.services.policies import FunctionPolicy, sine_sweep  # noqa: E402
from tendon.services.skill import check_compatibility, load_skill  # noqa: E402

SKILL = "skills/grasp/cube-sim"


@pytest.fixture
def body():
    """A live MuJoCo body, closed even when a test fails."""
    import tendon.drivers.mujoco  # noqa: F401  (registers the driver)

    driver = driver_base.load("mujoco")
    try:
        yield driver
    finally:
        driver.close()


def scripted_for(body: Driver) -> Policy:
    capability = body.capability
    return FunctionPolicy(
        sine_sweep(dof=capability.dof, amplitude=0.1),
        control_hz=capability.control_hz,
        dof=capability.dof,
    )


# ------------------------------------------------------------------------- the contract


def test_the_mujoco_driver_satisfies_the_protocol(body) -> None:
    """The stubs in the unit suite all agree with the kernel. This checks the real one."""
    assert isinstance(body, Driver)


def test_capability_is_reported_from_the_model(body) -> None:
    capability = body.capability
    assert capability.dof > 0
    assert capability.control_hz > 0
    assert capability.body_id.startswith("mujoco:")
    assert not capability.readonly


def test_apply_returns_what_was_executed(body) -> None:
    """The contract change that came out of Track A reading LeRobot.

    A driver returning `None` would discard the difference between what was commanded and
    what the actuator range allowed, and every episode would record the request as though
    it were the outcome.
    """
    from tendon.kernel.types import Action

    body.reset(seed=0)
    capability = body.capability
    action = Action(space=ActionSpace.JOINT_POSITION, values=[0.0] * capability.dof)

    applied = body.apply(action)
    assert applied is not None
    assert isinstance(applied, Action)
    assert len(applied.values) == capability.dof


def test_out_of_range_commands_come_back_clipped(body) -> None:
    """Real bodies clip. If this driver did not report the clipped value, the recorder
    would store a command the motors refused."""
    from tendon.kernel.types import Action

    body.reset(seed=0)
    capability = body.capability
    absurd = Action(space=ActionSpace.JOINT_POSITION, values=[1e6] * capability.dof)

    applied = body.apply(absurd)
    assert all(abs(v) < 1e5 for v in applied.values), (
        "an out-of-range command was accepted unchanged; the recording would be a fiction"
    )


def test_close_is_safe_to_call_twice(body) -> None:
    body.close()
    body.close()


# ------------------------------------------------------------------------------ episode


def test_an_episode_runs_against_the_real_body(body) -> None:
    result = Scheduler(driver=body, limits=SafetyLimits()).run_episode(
        scripted_for(body), max_steps=50, seed=0
    )
    assert result.steps == 50
    assert len(result.records) == 50


def test_the_body_actually_moves(body) -> None:
    """A loop that runs and changes nothing would pass every other test here."""
    start = body.reset(seed=0)
    Scheduler(driver=body, limits=SafetyLimits()).run_episode(
        scripted_for(body), max_steps=100, seed=0
    )
    end = body.observe()

    moved = max(
        abs(a - b)
        for a, b in zip(start.proprio.joint_positions, end.proprio.joint_positions, strict=True)
    )
    assert moved > 1e-4, "the arm did not move; the loop ran but commanded nothing"


def test_every_step_reaches_a_subscriber(body) -> None:
    """Design decision 1, against a real body rather than a stub."""
    seen: list[StepRecord] = []
    bus: Bus[StepRecord] = Bus()
    bus.subscribe("test", seen.append)

    Scheduler(driver=body, limits=SafetyLimits(), bus=bus).run_episode(
        scripted_for(body), max_steps=40, seed=0
    )

    assert len(seen) == 40
    assert all(r.applied is not None for r in seen)


def test_seeding_makes_a_run_repeatable(body) -> None:
    """Evaluation compares runs. Two runs from the same seed that differ would make every
    comparison noise."""
    scheduler = Scheduler(driver=body, limits=SafetyLimits())

    first = scheduler.run_episode(scripted_for(body), max_steps=30, seed=7)
    second = scheduler.run_episode(scripted_for(body), max_steps=30, seed=7)

    # Flattened: pytest.approx does not compare nested sequences.
    a = [v for r in first.records for v in r.applied.values]
    b = [v for r in second.records for v in r.applied.values]

    assert len(a) == len(b)
    assert a == pytest.approx(b)


# ------------------------------------------------------------------------------- safety


def test_a_velocity_limit_is_enforced_against_the_real_body(body) -> None:
    """The clamp path, end to end: policy asks for too much, safety reduces it, and the
    driver only ever sees the reduced command."""
    capability = body.capability
    ceiling = 0.5  # rad/s

    # A ramp that would move far faster than the ceiling allows.
    def ramp(step: int) -> list[float]:
        return [step * 0.05] + [0.0] * (capability.dof - 1)

    policy = FunctionPolicy(ramp, control_hz=capability.control_hz, dof=capability.dof)
    result = Scheduler(driver=body, limits=SafetyLimits(max_joint_velocity=ceiling)).run_episode(
        policy, max_steps=40, seed=0
    )

    assert any(r.clamped for r in result.records), "nothing was clamped"

    dt = 1.0 / capability.control_hz
    deltas = [
        abs(b.commanded.values[0] - a.commanded.values[0]) / dt
        for a, b in zip(result.records, result.records[1:], strict=False)
    ]
    assert max(deltas) <= ceiling * 1.01, "a command exceeded the limit after clamping"


def test_unevaluable_limits_are_reported_against_the_real_body(body) -> None:
    """Joint-space commands cannot be workspace-checked without forward kinematics, and
    the kernel deliberately has none. The run must say so rather than appear verified."""
    result = Scheduler(
        driver=body,
        limits=SafetyLimits(workspace_min=[-0.4, -0.4, 0.0], workspace_max=[0.4, 0.4, 0.5]),
    ).run_episode(scripted_for(body), max_steps=20, seed=0)

    assert any("workspace" in limit for limit in result.unchecked)
    assert result.unchecked[next(k for k in result.unchecked if "workspace" in k)] == 20


# -------------------------------------------------------------------------------- skill


def test_the_shipped_skill_is_compatible_with_the_shipped_body(body) -> None:
    """The pairing the repository ships. If these two do not match, the example in the
    README does not run, and that is exactly the mismatch this test exists to catch —
    `skill.yaml` asked for six arm axes against a five-axis arm until it was fixed.
    """
    reasons = check_compatibility(load_skill(SKILL), body)
    assert reasons == (), f"the shipped skill cannot run on the shipped body: {reasons}"
