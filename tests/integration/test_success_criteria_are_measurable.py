"""A skill names a quantity and a body supplies it, and nothing checked that it did.

`skills/grasp/cube-sim` asked for `cube_height` and the MuJoCo driver did not report it,
so `tendon eval` returned *unknown* for every episode it had ever run. Nothing failed.
The suite was green, the command exited 0, and the report printed a table of failure modes
whose single entry was `body does not report 'cube_height'`.

That is the whole second half of the v0.3 criterion — *did the task actually succeed* —
and it was missing for as long as the skill existed, because the two halves are written in
different files by different people and no test held them against each other.

Two invariants, opposite in direction:

- **Every criterion the skill names must be in `world_facts()`.** Otherwise the verdict is
  unknown and the milestone is unmeasurable, quietly.
- **No criterion may appear in `Observation.extra`.** That dictionary goes to the policy.
  A ground-truth quantity there is one the model can learn to read, and it will not exist
  on hardware — so the fix for the first invariant must not be to put the answer where the
  thing being graded can see it. The obvious fix and the wrong one are the same edit.

Skipped without the sim extra, which is the case where skipping is honest: the test cannot
run rather than choosing not to. CI's `recording` job installs every extra and runs this
whole directory, so nothing here is silently ungated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")

from tendon.kernel.protocols import MeasuresWorld  # noqa: E402
from tendon.services.bodies import open_body  # noqa: E402
from tendon.services.evaluator import SuccessCriterion  # noqa: E402
from tendon.services.skill import Skill, load_skill  # noqa: E402

#: The body these skills are evaluated on. `tendon eval` defaults to `mujoco`, so this is
#: the driver whose answer the milestone actually depends on.
SIM_BODY = "mujoco"

REPO = Path(__file__).resolve().parents[2]


def _skills() -> list[Skill]:
    return [load_skill(path.parent) for path in sorted((REPO / "skills").rglob("skill.yaml"))]


def _criteria(skill: Skill) -> list[SuccessCriterion]:
    return [SuccessCriterion.parse(name, value) for name, value in skill.success_criteria]


def _ids() -> list[str]:
    return [skill.ref for skill in _skills()]


@pytest.fixture(scope="module")
def body():
    driver = open_body(SIM_BODY)
    driver.reset(seed=0)
    try:
        yield driver
    finally:
        driver.close()


def test_there_are_skills_to_check() -> None:
    """Nothing below would fail if `skills/` were empty or unreadable."""
    skills = _skills()
    assert skills, "no skill.yaml found; the checks below would pass over nothing"
    assert any(_criteria(skill) for skill in skills), (
        "no skill declares success criteria, so neither invariant is exercised"
    )


def test_the_body_can_be_asked_about_the_world(body) -> None:
    """`MeasuresWorld` is optional on purpose — a driver need not implement it. But the
    body these skills are judged on must, or the first invariant is vacuous."""
    assert isinstance(body, MeasuresWorld)
    assert body.world_facts(), f"{SIM_BODY} reports no world facts at all"


@pytest.mark.parametrize("skill", _skills(), ids=_ids())
def test_every_success_criterion_is_reported_by_the_body(skill: Skill, body) -> None:
    """Otherwise `tendon eval` returns unknown for every episode, and says so only in a
    table headed by something else."""
    facts = body.world_facts()
    missing = [criterion.key for criterion in _criteria(skill) if criterion.key not in facts]

    assert not missing, (
        f"{skill.ref} is judged on {missing}, which {SIM_BODY} does not report; "
        f"it reports {sorted(facts)}. Every episode would be judged unknown."
    )


@pytest.mark.parametrize("skill", _skills(), ids=_ids())
def test_no_success_criterion_is_handed_to_the_policy(skill: Skill, body) -> None:
    """The counterweight. Satisfying the invariant above by adding the key to
    `Observation.extra` would publish the answer to the model being graded."""
    seen_by_policy = body.observe().extra
    leaked = [criterion.key for criterion in _criteria(skill) if criterion.key in seen_by_policy]

    assert not leaked, (
        f"{skill.ref} is graded on {leaked} and the policy reads the same keys from "
        f"Observation.extra. Ground truth a policy can read is ground truth it can learn "
        f"to use, and hardware has none."
    )


def test_the_criterion_actually_judges_rather_than_abstaining(body) -> None:
    """Present-and-unparseable reads the same as absent through `met_by`, so the check
    above passes on a key whose value is a string. This runs the real comparison."""
    facts = body.world_facts()
    for skill in _skills():
        for criterion in _criteria(skill):
            assert criterion.met_by(facts) is not None, (
                f"{skill.ref}: {criterion.key} is reported as {facts.get(criterion.key)!r}, "
                f"which yields no verdict"
            )
