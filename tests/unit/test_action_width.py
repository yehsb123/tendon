"""An action has to be as wide as the schema it will be recorded into.

`tendon run` spent its whole life failing at step 0 on a body with a gripper:

    subscriber recorder died at step 0: ValueError: The feature 'action' of shape
    '(5,)' does not have the expected shape '(6,)'

The recorder derives its schema from the body — `dof` joint channels plus one for the jaw
when the body has one. The baseline policy emitted `dof` values and left `Action.gripper`
as None. Five into six does not go, and the episode was dropped on its first frame.

## Why this was invisible

Every part behaved correctly. The bus isolates a subscriber that raises, so the body kept
moving, which is right — a consumer must never stop a robot. The scheduler recorded the
failure on the result, which is right. The command printed it. And then it exited zero,
having written a dataset containing nothing, because nothing in the run *depends* on the
recording succeeding.

So the check belongs here, before an episode: a policy and a body that disagree about the
width of an action cannot record, and that is knowable without opening a dataset.
`features_for` is importable without LeRobot precisely so this can be asked cheaply.
"""

from __future__ import annotations

import pytest

from tendon.kernel.types import (
    ActionSpace,
    Capability,
    GripperKind,
    Observation,
    Proprioception,
)
from tendon.services.policies import ScriptedPolicy, sine_sweep
from tendon.services.recorder import features_for


def capability(*, dof: int, gripper: GripperKind) -> Capability:
    return Capability(
        body_id="test_body",
        dof=dof,
        control_hz=100.0,
        gripper=gripper,
        action_spaces=(ActionSpace.JOINT_POSITION,),
        simulated=True,
    )


def schema_width(cap: Capability) -> int:
    """How many action channels the recorder will demand for this body."""
    return features_for(cap)["action"]["shape"][0]


def emitted_width(action) -> int:
    """How many channels an action actually carries once written."""
    return len(action.values) + (0 if action.gripper is None else 1)


def first_action(cap: Capability, *, gripper: float | None):
    policy = ScriptedPolicy(
        sine_sweep(dof=cap.dof),
        control_hz=cap.control_hz,
        dof=cap.dof,
        gripper=gripper,
    )
    observation = Observation(step=0, proprio=Proprioception(joint_positions=[0.0] * cap.dof))
    return policy.predict(observation).actions[0]


# ------------------------------------------------------------------ the schema


def test_a_body_with_a_jaw_wants_one_more_channel_than_it_has_joints() -> None:
    assert schema_width(capability(dof=5, gripper=GripperKind.PARALLEL)) == 6


def test_a_body_without_one_wants_exactly_its_joints() -> None:
    assert schema_width(capability(dof=5, gripper=GripperKind.NONE)) == 5


# ------------------------------------------------------------- and what fits it


def test_the_baseline_policy_fills_a_jaw_body_when_told_to() -> None:
    """The fix. `tendon run` passes a held-open jaw for any body that has one."""
    cap = capability(dof=5, gripper=GripperKind.PARALLEL)
    assert emitted_width(first_action(cap, gripper=1.0)) == schema_width(cap)


def test_the_baseline_policy_fits_a_body_without_a_jaw() -> None:
    cap = capability(dof=5, gripper=GripperKind.NONE)
    assert emitted_width(first_action(cap, gripper=None)) == schema_width(cap)


def test_leaving_the_jaw_unset_on_a_jaw_body_is_the_bug_that_was_there() -> None:
    """Held as an explicit statement of what used to happen, so that a change which
    reintroduces it fails here — with a sentence about widths — rather than inside a
    LeRobot exception at step 0 of somebody's run."""
    cap = capability(dof=5, gripper=GripperKind.PARALLEL)

    assert emitted_width(first_action(cap, gripper=None)) == 5
    assert schema_width(cap) == 6


@pytest.mark.parametrize("dof", [1, 5, 6, 7])
@pytest.mark.parametrize("gripper", [GripperKind.NONE, GripperKind.PARALLEL])
def test_the_two_agree_across_bodies(dof: int, gripper: GripperKind) -> None:
    """The contract itself, over the shapes a driver might declare."""
    cap = capability(dof=dof, gripper=gripper)
    held = None if gripper is GripperKind.NONE else 1.0

    assert emitted_width(first_action(cap, gripper=held)) == schema_width(cap)
