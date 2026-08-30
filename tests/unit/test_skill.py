"""Loading a skill, and refusing to run it on a body that cannot do the job.

The compatibility check is the part worth testing hardest. It exists so that a mismatch is
a load-time failure — discovering one at step 40 means a robot is already moving when the
problem is found.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tendon.kernel.types import ActionSpace, Capability, GripperKind
from tendon.services.skill import (
    IncompatibleBody,
    SkillError,
    check_compatibility,
    load_skill,
    require_compatible,
)

REPO = Path(__file__).resolve().parents[2]
CUBE_SIM = REPO / "skills" / "grasp" / "cube-sim"

MINIMAL = """
apiVersion: tendon/v1alpha1
kind: Skill
metadata:
  name: probe
  namespace: test
  version: 0.1.0
"""


class Body:
    """A body with whatever capabilities a test needs."""

    def __init__(
        self,
        *,
        dof: int = 5,
        gripper: GripperKind = GripperKind.PARALLEL,
        control_hz: float = 100.0,
        cameras: tuple[str, ...] = ("wrist",),
        accepts: tuple[ActionSpace, ...] = (ActionSpace.JOINT_POSITION,),
        readonly: bool = False,
    ) -> None:
        self._cap = Capability(
            body_id="test:body",
            dof=dof,
            gripper=gripper,
            control_hz=control_hz,
            cameras=cameras,
            readonly=readonly,
        )
        self._accepts = accepts

    @property
    def capability(self) -> Capability:
        return self._cap

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        return self._accepts

    def reset(self, *, seed: int | None = None):  # pragma: no cover - not exercised
        raise NotImplementedError

    def observe(self):  # pragma: no cover
        raise NotImplementedError

    def apply(self, action):  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        pass


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "skill.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ------------------------------------------------------------------------------ loading


def test_the_shipped_skill_loads() -> None:
    """If the reference skill does not load, the format does not work."""
    skill = load_skill(CUBE_SIM)
    assert skill.ref == "grasp/cube-sim"
    assert skill.limits.max_joint_velocity == pytest.approx(1.5)
    assert skill.confidence_threshold == pytest.approx(0.5)
    assert skill.requires.gripper is GripperKind.PARALLEL


def test_a_directory_resolves_to_its_skill_file() -> None:
    assert load_skill(CUBE_SIM).name == load_skill(CUBE_SIM / "skill.yaml").name


def test_a_missing_file_is_reported_with_its_path(tmp_path: Path) -> None:
    with pytest.raises(SkillError, match="no skill file"):
        load_skill(tmp_path / "nothing")


def test_broken_yaml_is_reported_as_such(tmp_path: Path) -> None:
    with pytest.raises(SkillError, match="not valid YAML"):
        load_skill(write(tmp_path, "metadata: [unclosed"))


def test_an_unknown_api_version_is_refused(tmp_path: Path) -> None:
    """A file written for a later tendon should say so rather than half-load."""
    with pytest.raises(SkillError, match="apiVersion"):
        load_skill(write(tmp_path, MINIMAL.replace("v1alpha1", "v9")))


@pytest.mark.parametrize("field", ["name", "namespace", "version"])
def test_missing_identity_is_refused(tmp_path: Path, field: str) -> None:
    body = "\n".join(ln for ln in MINIMAL.split("\n") if not ln.strip().startswith(field))
    with pytest.raises(SkillError, match=field):
        load_skill(write(tmp_path, body))


def test_defaults_apply_when_optional_blocks_are_absent(tmp_path: Path) -> None:
    skill = load_skill(write(tmp_path, MINIMAL))
    assert skill.confidence_threshold == pytest.approx(0.5)
    assert skill.limits.max_joint_velocity is None
    assert skill.requires.dof is None


# ---------------------------------------------------------------------- typo protection


def test_a_misspelled_safety_key_is_refused(tmp_path: Path) -> None:
    """The one typo worth refusing over.

    `max_joint_velocty` would leave the limit unset, the skill would run unbounded, and
    nothing downstream reports a limit as missing — it would simply never fire.
    """
    body = MINIMAL + "\nsafety:\n  max_joint_velocty: 1.5\n"
    with pytest.raises(SkillError, match="unknown key"):
        load_skill(write(tmp_path, body))


def test_an_unknown_top_level_block_is_tolerated(tmp_path: Path) -> None:
    """Permissive about structure we do not know, strict about limits we do.

    An unknown top-level key is far more likely to be a feature from a later version than
    a typo worth refusing over.
    """
    load_skill(write(tmp_path, MINIMAL + "\nfuture_feature:\n  enabled: true\n"))


@pytest.mark.parametrize("value", ["1.4", "-0.1", "not-a-number"])
def test_an_out_of_range_threshold_is_refused(tmp_path: Path, value: str) -> None:
    body = MINIMAL + f"\ninterrupt:\n  confidence_threshold: {value}\n"
    with pytest.raises(SkillError):
        load_skill(write(tmp_path, body))


# ------------------------------------------------------------------------ compatibility


def test_the_shipped_skill_runs_on_a_matching_body() -> None:
    assert check_compatibility(load_skill(CUBE_SIM), Body()) == ()


def test_too_few_axes_is_reported() -> None:
    reasons = check_compatibility(load_skill(CUBE_SIM), Body(dof=3))
    assert any("degrees of freedom" in r for r in reasons)


def test_the_gripper_is_not_counted_in_dof() -> None:
    """SO-ARM100 has five arm joints plus a jaw.

    `Capability.dof` excludes the jaw because `Action.gripper` carries it separately.
    Counting it would let a skill needing six arm axes match a five-joint arm — which is
    exactly what the shipped skill asked for before this was fixed.
    """
    skill = load_skill(CUBE_SIM)
    assert skill.requires.dof == 5
    assert check_compatibility(skill, Body(dof=5)) == ()


def test_a_wrong_gripper_is_reported() -> None:
    reasons = check_compatibility(load_skill(CUBE_SIM), Body(gripper=GripperKind.SUCTION))
    assert any("gripper" in r for r in reasons)


def test_a_missing_camera_is_reported() -> None:
    reasons = check_compatibility(load_skill(CUBE_SIM), Body(cameras=()))
    assert any("camera" in r for r in reasons)


def test_an_unsupported_action_space_is_reported() -> None:
    reasons = check_compatibility(load_skill(CUBE_SIM), Body(accepts=(ActionSpace.EE_ABS_POSE,)))
    assert any("action" in r or "joint_position" in r for r in reasons)


def test_a_faster_body_is_fine_and_a_slower_one_is_not() -> None:
    """A skill recorded at 50Hz replayed at 20Hz plays in slow motion — a different
    trajectory wearing the same numbers."""
    skill = load_skill(CUBE_SIM)
    assert check_compatibility(skill, Body(control_hz=200.0)) == ()
    assert any("Hz" in r for r in check_compatibility(skill, Body(control_hz=20.0)))


def test_a_read_only_body_cannot_run_a_skill() -> None:
    """The human-video driver produces observations and accepts no commands."""
    reasons = check_compatibility(load_skill(CUBE_SIM), Body(readonly=True))
    assert any("read-only" in r for r in reasons)


def test_every_problem_is_reported_at_once() -> None:
    """Reporting the first and stopping would make configuring a new body a sequence of
    runs, each revealing one more thing."""
    reasons = check_compatibility(
        load_skill(CUBE_SIM),
        Body(dof=2, gripper=GripperKind.SUCTION, cameras=(), control_hz=10.0),
    )
    assert len(reasons) >= 4


def test_require_compatible_raises_with_every_reason() -> None:
    with pytest.raises(IncompatibleBody) as excinfo:
        require_compatible(load_skill(CUBE_SIM), Body(dof=1, cameras=()))

    assert len(excinfo.value.reasons) >= 2
    assert "test:body" in str(excinfo.value)


# ---------------------------------------------------------- the baseline field


def test_a_skill_can_say_how_to_attempt_it_without_a_model(tmp_path: Path) -> None:
    """`policy.baseline` exists because evaluation had no way to ask.

    `tendon eval grasp/cube-sim` judged a sine sweep against the skill's success condition
    — was the cube lifted — and reported failure modes for a motion that never reached for
    it. The skill knew what success meant and had no way to say what should be attempted.
    """
    path = tmp_path / "skill.yaml"
    path.write_text(MINIMAL + "policy:\n  baseline: cube-pick\n", encoding="utf-8")

    assert load_skill(path).policy_baseline == "cube-pick"


def test_a_skill_without_one_says_nothing_rather_than_guessing(tmp_path: Path) -> None:
    """None, not a default. A skill with nothing to attempt should not be given something
    to attempt on its behalf."""
    path = tmp_path / "skill.yaml"
    path.write_text(MINIMAL, encoding="utf-8")

    assert load_skill(path).policy_baseline is None


def test_the_shipped_skill_declares_one() -> None:
    """The skill this repository ships is the one the milestone is measured on, and it is
    a grasp. Evaluating it with a sweep is what prompted the field."""
    repo = Path(__file__).resolve().parents[2]
    assert load_skill(repo / "skills/grasp/cube-sim").policy_baseline == "cube-pick"


# ------------------------------------------------------------- finding a skill
#
# Everything else in the project calls a skill `namespace/name`: the API serves
# `/api/skills/{namespace}/{name}`, the shell lists it, the run output prints it, the
# README documents `tendon run <skill>`. Only the command line insisted on a path, so the
# documented form produced `no skill file at grasp\cube-sim` — an error about paths, for
# somebody who was not thinking about paths.


@pytest.fixture
def skill_root(tmp_path: Path, monkeypatch) -> Path:
    """A skills tree somewhere else, so these do not depend on the current directory."""
    import tendon.services.skill as skill_module

    root = tmp_path / "skills"
    directory = root / "test" / "probe"
    directory.mkdir(parents=True)
    (directory / "skill.yaml").write_text(MINIMAL, encoding="utf-8")

    monkeypatch.setattr(skill_module, "SKILL_ROOT", root)
    monkeypatch.chdir(tmp_path)
    return root


def test_a_reference_resolves_under_the_skill_root(skill_root: Path) -> None:
    assert load_skill("test/probe").ref == "test/probe"


def test_a_path_is_tried_first(skill_root: Path, tmp_path: Path) -> None:
    """Precedence, tested where it actually matters: one string that is both a real
    relative path and a valid reference.

    A reference is an addition, so anything that used to resolve has to keep resolving to
    the same file. Redirecting an existing caller to a different skill that happens to
    share a name would be a worse bug than the one this resolution fixes.
    """
    here = tmp_path / "test" / "probe"
    here.mkdir(parents=True)
    (here / "skill.yaml").write_text(MINIMAL.replace("0.1.0", "9.9.9"), encoding="utf-8")

    assert load_skill("test/probe").version == "9.9.9"


def test_a_missing_reference_names_both_places_it_looked(skill_root: Path) -> None:
    """Reporting only the path the caller typed would hide the search; reporting only the
    root would hide what they asked for."""
    with pytest.raises(SkillError) as excinfo:
        load_skill("grasp/nowhere")

    message = str(excinfo.value)
    assert "grasp" in message and "nowhere" in message
    assert "skills" in message


def test_a_missing_path_is_reported_as_a_path(skill_root: Path) -> None:
    """Three segments is not a reference. Someone who typed a path wants to hear about
    the path they typed, not about a skill root they never mentioned."""
    with pytest.raises(SkillError) as excinfo:
        load_skill("some/deep/path")

    assert "skills" not in str(excinfo.value)
