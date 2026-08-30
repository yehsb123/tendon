"""`services/policy_lerobot.py`, the adapter between a checkpoint and an `Intent`.

Every test here is a regression. Three bugs came out of running the adapter against real
weights rather than a test double, and each one was invisible to the double because the
double was written by the same person who wrote the adapter:

1. A deterministic policy scored confidence 1.0. ACT returns the same chunk every time,
   the spread was zero, and zero spread reads as certainty. That is a policy that can
   never raise an interrupt wearing the number that says it never needs to.
2. The batch was written with only the plural camera key. `lerobot/diffusion_pusht`
   declares a singular `observation.image` and raised `KeyError` from inside the policy.
3. A policy conditioning on more than one observation step failed with an einops shape
   error three frames deep, saying nothing about the cause.

The fakes below produce the *shapes* real checkpoints produce, including the padding, so
they exercise the same paths. They do not need torch except where a test says so.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from tendon.kernel.types import ConfidenceSource, Observation, Proprioception
from tendon.services.policy_lerobot import LeRobotPolicy, PolicyError

DOF = 5
CHUNK = 8
#: SmolVLA pads its action head to this. A five-joint arm with a gripper uses six columns
#: and the rest is padding, which the adapter has to drop rather than command.
PADDED_DIMS = 32

#: `_build_batch` turns an observation into torch tensors, because that is what a LeRobot
#: policy consumes. Everything downstream of it therefore needs torch, and the CI unit job
#: installs only the dev extra. The construction and protocol checks below do not, and
#: those are the ones that still run there.
#:
#: `find_spec` rather than a try/except import: blocking an import does not unload a module
#: that is already in `sys.modules`, which is how a local run of this file passed while CI
#: failed on every test that calls `predict`.
requires_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="predict builds torch tensors; needs the train extra",
)


class FakeConfig:
    """Enough of a LeRobot policy config for the adapter to read."""

    def __init__(self, image_keys: tuple[str, ...] = (), n_obs_steps: int = 1) -> None:
        self.input_features = dict.fromkeys(image_keys, object())
        self.n_obs_steps = n_obs_steps


class FakePolicy:
    """Emits chunks shaped like a real checkpoint's, with controllable variation."""

    def __init__(
        self,
        *,
        scatter: float = 0.0,
        image_keys: tuple[str, ...] = (),
        n_obs_steps: int = 1,
        dims: int = PADDED_DIMS,
        steps: int = CHUNK,
    ) -> None:
        self.config = FakeConfig(image_keys, n_obs_steps)
        self._scatter = scatter
        self._dims = dims
        self._steps = steps
        self.batches: list[dict[str, Any]] = []
        self.calls = 0

    def predict_action_chunk(self, batch: dict[str, Any], noise: Any = None) -> Any:
        import numpy as np

        self.calls += 1
        self.batches.append(batch)
        chunk = np.zeros((1, self._steps, self._dims), dtype="float32")
        # A chunk narrower than the body is a case the adapter has to reject, so the fake
        # has to be able to produce one without indexing past its own array first.
        chunk[0, :, : min(DOF, self._dims)] = 0.25
        if self._dims > DOF:
            chunk[0, :, DOF] = 0.7  # gripper column
        if self._scatter:
            rng = np.random.default_rng(self.calls)
            chunk = chunk + rng.normal(0.0, self._scatter, chunk.shape).astype("float32")
        return chunk

    def reset(self) -> None:
        pass


def make(policy: FakePolicy, **kwargs: Any) -> LeRobotPolicy:
    defaults: dict[str, Any] = {
        "name": "test/policy",
        "task": "pick up the cube",
        "dof": DOF,
        "control_hz": 100.0,
        "reference_spread": 0.02,
    }
    defaults.update(kwargs)
    return LeRobotPolicy(policy, **defaults)


@pytest.fixture
def observation() -> Observation:
    return Observation(
        step=0,
        proprio=Proprioception(joint_positions=[0.0] * DOF, gripper_open=0.5),
    )


# ------------------------------------------------------------------ construction


def test_a_policy_needing_observation_history_is_refused_at_construction() -> None:
    """Regression: diffusion_pusht wants two steps and failed deep inside einops.

    Refused here, where the message can say what is wrong, rather than at the first
    prediction where it surfaces as a shape mismatch in a vendor package.
    """
    with pytest.raises(PolicyError) as caught:
        make(FakePolicy(n_obs_steps=2))
    assert "observation steps" in str(caught.value)


@pytest.mark.parametrize("bad", [{"control_hz": 0.0}, {"control_hz": -1.0}, {"dof": 0}])
def test_impossible_bodies_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        make(FakePolicy(), **bad)


# -------------------------------------------------------------------- confidence


@requires_torch
def test_a_deterministic_policy_reports_no_confidence(observation: Observation) -> None:
    """The bug a real checkpoint found, held down.

    A policy returning identical chunks has a spread of zero. Scoring that 1.0 produces a
    policy that can never hand over, wearing the number that means it never has to. It is
    detected from the samples already drawn, so it is caught whether or not the caller
    knew to declare it.
    """
    intent = make(FakePolicy(scatter=0.0)).predict(observation)

    assert intent.confidence.source is ConfidenceSource.NONE
    assert intent.confidence.score == 0.0
    assert intent.confidence.reasons, "a refusal with no reason cannot be acted on"


@requires_torch
def test_a_scattered_policy_scores_below_an_agreeing_one(observation: Observation) -> None:
    agreeing = make(FakePolicy(scatter=0.0005)).predict(observation)
    scattered = make(FakePolicy(scatter=0.5)).predict(observation)

    assert agreeing.confidence.source is ConfidenceSource.CHUNK_VARIANCE
    assert scattered.confidence.source is ConfidenceSource.CHUNK_VARIANCE
    assert scattered.confidence.score < agreeing.confidence.score
    assert scattered.confidence.reasons, "a low score has to say why"


@requires_torch
def test_too_few_samples_is_reported_as_no_measurement(observation: Observation) -> None:
    """One sample cannot support a spread, and saying so is the point."""
    intent = make(FakePolicy(scatter=0.5), samples=1).predict(observation)
    assert intent.confidence.source is ConfidenceSource.NONE


@requires_torch
def test_confidence_costs_one_forward_pass_per_sample(observation: Observation) -> None:
    policy = FakePolicy(scatter=0.01)
    make(policy, samples=3).predict(observation)
    assert policy.calls == 3


# ------------------------------------------------------------------------ shape


@requires_torch
def test_padding_is_dropped_and_the_gripper_split_out(observation: Observation) -> None:
    """SmolVLA pads to 32 dimensions. Reading them as joints commands axes that do not exist."""
    intent = make(FakePolicy(scatter=0.01)).predict(observation)
    action = intent.actions[0]

    assert len(action.values) == DOF
    assert action.gripper == pytest.approx(0.7, abs=0.05)


@requires_torch
def test_a_policy_narrower_than_the_body_is_refused(observation: Observation) -> None:
    with pytest.raises(PolicyError) as caught:
        make(FakePolicy(scatter=0.01, dims=3)).predict(observation)
    assert "dimensions" in str(caught.value)


@requires_torch
def test_the_horizon_is_the_chunk_against_the_body_rate(observation: Observation) -> None:
    """A chunk length means nothing without a rate; the shell shows the seconds."""
    intent = make(FakePolicy(scatter=0.01, steps=50), control_hz=100.0).predict(observation)
    assert len(intent.actions) == 50
    assert intent.horizon_s == pytest.approx(0.5)


@requires_torch
def test_the_goal_reaches_the_operator(observation: Observation) -> None:
    intent = make(FakePolicy(scatter=0.01)).predict(observation)
    assert intent.goal == "pick up the cube"


# ----------------------------------------------------------------------- cameras


@requires_torch
def test_frames_go_to_the_key_the_checkpoint_declares(observation: Observation) -> None:
    """Regression: the adapter wrote only the plural form.

    A driver names its camera `wrist` and a checkpoint trained elsewhere calls the same
    view `top`. Refusing on that mismatch would be right only if the names meant
    something, so the frame follows the checkpoint's declaration by position.
    """
    import numpy as np

    policy = FakePolicy(scatter=0.01, image_keys=("observation.images.top",))
    adapter = make(policy, frames=lambda: {"wrist": np.zeros((8, 8, 3), dtype="uint8")})
    adapter.predict(observation)

    assert "observation.images.top" in policy.batches[0]
    assert "observation.images.wrist" not in policy.batches[0]


@requires_torch
def test_a_singular_camera_key_is_honoured(observation: Observation) -> None:
    """`lerobot/diffusion_pusht` declares `observation.image`, with no name."""
    import numpy as np

    policy = FakePolicy(scatter=0.01, image_keys=("observation.image",))
    adapter = make(policy, frames=lambda: {"image": np.zeros((8, 8, 3), dtype="uint8")})
    adapter.predict(observation)

    assert "observation.image" in policy.batches[0]


@requires_torch
def test_a_policy_declaring_nothing_gets_the_plural_convention(observation: Observation) -> None:
    """Which is what tendon's own recorder writes."""
    import numpy as np

    policy = FakePolicy(scatter=0.01)
    adapter = make(policy, frames=lambda: {"wrist": np.zeros((8, 8, 3), dtype="uint8")})
    adapter.predict(observation)

    assert "observation.images.wrist" in policy.batches[0]


@requires_torch
def test_the_state_carries_the_gripper_when_the_body_has_one(observation: Observation) -> None:
    policy = FakePolicy(scatter=0.01)
    make(policy).predict(observation)
    state = policy.batches[0]["observation.state"]

    assert tuple(state.shape) == (1, DOF + 1), "batch dimension, joints, then the gripper"


# ---------------------------------------------------------------------- protocol


def test_the_adapter_satisfies_the_kernel_protocol() -> None:
    """The scheduler must not be able to tell this from a scripted controller."""
    from tendon.kernel.protocols import Policy

    assert isinstance(make(FakePolicy()), Policy)
