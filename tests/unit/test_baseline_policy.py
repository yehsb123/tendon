"""Which policy runs when there are no weights, and why a skill gets to say.

`tendon eval grasp/cube-sim` judged its results against the skill's success condition —
*was the cube lifted above 0.1 m* — while running a sine sweep on one joint. It reported an
intervention rate and failure modes for a motion that never reached for the cube. The
command claimed to evaluate a skill and evaluated something else on the same body.

Nothing was wrong in either piece. `_baseline_policy` correctly built the only baseline it
knew about, and the skill correctly declared what success meant. What was missing was a way
for the skill to say *how to attempt this without a model*, so `policy.baseline` is now part
of the format.

## Why a name and not an import path

A skill file is meant to be shared and installed (v0.4). A field naming a Python object
would let a downloaded skill choose what code runs in the process that opened it. So the
set is closed and lives here, and a skill asking for something unknown is refused with the
list of what exists rather than silently falling back to a sweep — falling back is how the
original problem would come back wearing a different name.
"""

from __future__ import annotations

import pytest
import typer

from tendon.cli.policies import _BASELINES, _baseline_policy
from tendon.kernel.types import ActionSpace, Capability, GripperKind
from tendon.services.skill import Skill


def capability(*, dof: int = 5, gripper: GripperKind = GripperKind.PARALLEL) -> Capability:
    return Capability(
        body_id="test_body",
        dof=dof,
        control_hz=100.0,
        gripper=gripper,
        action_spaces=(ActionSpace.JOINT_POSITION,),
        simulated=True,
    )


def skill(baseline: str | None) -> Skill:
    return Skill(namespace="grasp", name="cube-sim", version="0.1.0", policy_baseline=baseline)


# ------------------------------------------------------------------- what is chosen


def test_a_skill_that_names_a_baseline_gets_the_one_that_attempts_the_task() -> None:
    policy = _baseline_policy(skill("cube-pick"), capability())

    assert type(policy).__module__ == "tendon.services.policy_scripted"


def test_a_skill_that_names_none_gets_the_sweep() -> None:
    """Unchanged, and correct for a skill with nothing to attempt. A body motion that runs
    is still what you want for exercising the loop, the safety path and the recorder."""
    policy = _baseline_policy(skill(None), capability())

    assert type(policy).__module__ == "tendon.services.policies"


def test_the_policy_is_named_after_the_skill_it_stands_in_for() -> None:
    """It appears in the run output and in the recorded task string, and `scripted` alone
    does not say which skill produced the episode."""
    assert "grasp/cube-sim" in _baseline_policy(skill("cube-pick"), capability()).name


# ------------------------------------------------------------------ and what is not


def test_an_unknown_baseline_is_refused_with_the_list() -> None:
    """Refused rather than fallen back on.

    A silent fallback would put the sweep behind a skill that asked for something else,
    which is the original bug with a different cause — and the failure would show up as a
    strange evaluation rather than as an error.
    """
    with pytest.raises(typer.BadParameter) as excinfo:
        _baseline_policy(skill("cube-pick-v2"), capability())

    message = str(excinfo.value)
    assert "cube-pick-v2" in message
    assert "cube-pick" in message


def test_the_known_set_is_small_and_closed() -> None:
    """Stated as a test because the tempting change is to accept an import path, and a
    skill file that names a Python object chooses what runs in the process that opens it.
    Skills are meant to be downloaded."""
    assert sorted(_BASELINES) == ["cube-pick"]


# -------------------------------------------------------------- and the jaw, still


def test_the_sweep_still_commands_the_jaw_of_a_body_that_has_one() -> None:
    """The fix from three rounds ago, held down while this function grew a branch. Without
    a jaw value the action is a channel narrower than the recorder's schema and every
    episode dies at step 0."""
    from tendon.kernel.types import Observation, Proprioception

    policy = _baseline_policy(skill(None), capability(gripper=GripperKind.PARALLEL))
    observation = Observation(step=0, proprio=Proprioception(joint_positions=[0.0] * 5))

    assert policy.predict(observation).actions[0].gripper is not None


def test_a_body_without_a_jaw_is_not_given_one() -> None:
    from tendon.kernel.types import Observation, Proprioception

    policy = _baseline_policy(skill(None), capability(gripper=GripperKind.NONE))
    observation = Observation(step=0, proprio=Proprioception(joint_positions=[0.0] * 5))

    assert policy.predict(observation).actions[0].gripper is None
