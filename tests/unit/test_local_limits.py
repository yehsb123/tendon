"""A site can put a ceiling over what a skill asks for.

`SECURITY.md`, under *skills are remote code*: a skill declares its own safety limits, so an
installed skill proposes the bounds it runs under, and `tendon install` fetches from the
Hub. A namespace you do not control could ship a `skill.yaml` with whatever
`max_joint_velocity` it liked, and nothing on the machine could say otherwise. The document
has tracked this as required work since a physical driver landed.

## The direction is the feature

The local file only ever tightens. A site says "nothing here moves faster than 2 rad/s
whatever it claims to need", and no skill widens that by asking. A local file that could
loosen a skill's own bound would be a way to disable a safety limit by editing a config,
which is what this exists to prevent — so the tests below spend more effort on that
direction than on the one somebody would think to write first.

## An absent file is not a permission

It means no ceiling was configured, so the skill's limits stand. That is what every
installation does today, and saying it out loud keeps it from being read as "unlimited".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tendon.kernel.types import SafetyLimits
from tendon.services.limits import LocalLimitsError, load_local_limits, tighten


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------- tightening


def test_the_stricter_velocity_wins() -> None:
    result = tighten(SafetyLimits(max_joint_velocity=4.0), SafetyLimits(max_joint_velocity=2.0))

    assert result.max_joint_velocity == 2.0


def test_a_skill_cannot_widen_the_ceiling() -> None:
    """The direction that matters. A skill asking for more than the site allows gets the
    site's number, not its own."""
    result = tighten(SafetyLimits(max_joint_velocity=99.0), SafetyLimits(max_joint_velocity=2.0))

    assert result.max_joint_velocity == 2.0


def test_a_ceiling_cannot_loosen_a_skill() -> None:
    """The mirror image, and the one that would turn this feature into a way to disable a
    limit by editing a file."""
    result = tighten(SafetyLimits(max_joint_velocity=1.0), SafetyLimits(max_joint_velocity=50.0))

    assert result.max_joint_velocity == 1.0


def test_a_limit_only_one_side_sets_is_used() -> None:
    """A ceiling that names a velocity and nothing else is a site with an opinion about
    velocity, not a site declaring force unbounded."""
    result = tighten(
        SafetyLimits(max_joint_velocity=4.0, max_force=10.0),
        SafetyLimits(max_joint_velocity=2.0),
    )

    assert result.max_joint_velocity == 2.0
    assert result.max_force == 10.0


def test_no_ceiling_leaves_the_skill_alone() -> None:
    """Every installation today. An absent file is not a permission — it is the absence of
    an opinion, and the skill's own limits stand."""
    skill = SafetyLimits(max_joint_velocity=4.0, max_force=10.0)

    assert tighten(skill, None) == skill


# ------------------------------------------------------------------- a workspace


def test_the_workspace_shrinks_to_the_overlap() -> None:
    """Boxes intersect rather than replace: the lower corner takes the larger value and the
    upper corner the smaller, so the result is inside both."""
    result = tighten(
        SafetyLimits(workspace_min=[-1.0, -1.0], workspace_max=[1.0, 1.0]),
        SafetyLimits(workspace_min=[-0.5, -2.0], workspace_max=[2.0, 0.5]),
    )

    assert result.workspace_min == [-0.5, -1.0]
    assert result.workspace_max == [1.0, 0.5]


def test_a_ceiling_can_shrink_one_axis_without_restating_the_others() -> None:
    result = tighten(
        SafetyLimits(workspace_min=[-1.0, -1.0], workspace_max=[1.0, 1.0]),
        SafetyLimits(workspace_max=[0.2, 1.0]),
    )

    assert result.workspace_min == [-1.0, -1.0]
    assert result.workspace_max == [0.2, 1.0]


# ---------------------------------------------------------------------- reading


def test_a_missing_file_is_no_ceiling(tmp_path: Path) -> None:
    assert load_local_limits(tmp_path / "nothing.yaml") is None


def test_a_file_is_read(tmp_path: Path) -> None:
    path = write(tmp_path / "limits.yaml", "safety:\n  max_joint_velocity: 2.0\n")

    limits = load_local_limits(path)
    assert limits is not None
    assert limits.max_joint_velocity == 2.0


def test_the_safety_key_is_optional(tmp_path: Path) -> None:
    """A file that is only limits does not need a heading saying so."""
    path = write(tmp_path / "limits.yaml", "max_joint_velocity: 2.0\n")

    limits = load_local_limits(path)
    assert limits is not None
    assert limits.max_joint_velocity == 2.0


def test_an_empty_file_is_no_ceiling(tmp_path: Path) -> None:
    assert load_local_limits(write(tmp_path / "limits.yaml", "\n")) is None


def test_a_malformed_file_raises_rather_than_being_ignored(tmp_path: Path) -> None:
    """The most important behaviour in this module.

    A ceiling that silently did nothing would leave a site believing it has a bound it does
    not have. Having no limits file is a decision; having a broken one is a mistake, and the
    two must not look the same from inside a running system.
    """
    path = write(tmp_path / "limits.yaml", "safety: [not, a, mapping]\n")

    with pytest.raises(LocalLimitsError):
        load_local_limits(path)


def test_an_impossible_limit_raises(tmp_path: Path) -> None:
    """`SafetyLimits` requires a positive velocity. A zero would parse as "cannot move" and
    a negative as nothing at all, and neither is what somebody meant to write."""
    path = write(tmp_path / "limits.yaml", "safety:\n  max_joint_velocity: -1\n")

    with pytest.raises(LocalLimitsError):
        load_local_limits(path)


def test_unreadable_yaml_raises(tmp_path: Path) -> None:
    path = write(tmp_path / "limits.yaml", "safety:\n  max_joint_velocity: [\n")

    with pytest.raises(LocalLimitsError):
        load_local_limits(path)


# ------------------------------------------------- and nothing bypasses it


def test_no_scheduler_is_given_a_skill_s_limits_directly() -> None:
    """A ceiling that three call sites honour and a fourth does not is not a ceiling.

    There are three places a `Scheduler` is built, and this project has already shipped a
    bug where the same construction existed twice and only one copy was fixed. Checked at
    the source: `limits=loaded.limits` is the shape that skips the ceiling entirely.
    """
    repo = Path(__file__).resolve().parents[2]

    offenders = [
        path.relative_to(repo)
        for path in (repo / "src" / "tendon").rglob("*.py")
        if "limits=loaded.limits" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        f"{offenders} pass a skill's own limits to a scheduler, which skips the machine's "
        "ceiling. Route them through the same place the others use."
    )


def test_every_scheduler_gets_limits_from_somewhere_that_tightens() -> None:
    """The positive half, so the check above cannot pass by there being no schedulers."""
    import ast

    repo = Path(__file__).resolve().parents[2]
    built = 0

    for path in (repo / "src" / "tendon").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Scheduler"
            ):
                built += 1
                names = {kw.arg for kw in node.keywords}
                assert "limits" in names, f"{path} builds a Scheduler with no limits at all"

    assert built >= 3, f"only found {built} Scheduler constructions; the scan has gone stale"
