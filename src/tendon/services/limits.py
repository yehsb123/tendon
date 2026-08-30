"""A site's ceiling over whatever a skill asks for.

`SECURITY.md` has listed this as required work since a physical driver landed, under the
heading *skills are remote code*: a skill declares its own safety limits, so an installed
skill proposes the bounds it runs under. `tendon install` fetches from the Hub. A namespace
you do not control can therefore ship a `skill.yaml` whose `max_joint_velocity` is whatever
it likes, and nothing on the machine could say otherwise.

## Tighter only, never looser

The local file is a **ceiling**, not a replacement. The effective limit is the stricter of
the two, and the direction is the whole point: a site can say "nothing here moves faster
than 2 rad/s whatever it claims to need", and no skill can widen that by asking.

The reverse — a local file that loosened a skill's own bound — would be a way to disable a
safety limit by editing a config, which is what this exists to prevent.

An absent local file is not a permission. It means no ceiling was configured, so the skill's
own limits stand, exactly as before. That is the current behaviour for every installation
and it is stated rather than left implicit.

## What it cannot do

Invent a limit the kernel cannot check. `safety.check` reports what it could not evaluate —
a workspace bound means nothing against a joint-space command without forward kinematics —
and a ceiling written here inherits that. Tightening an unenforceable limit produces a
tighter unenforceable limit.
"""

from __future__ import annotations

from pathlib import Path

from tendon.kernel.types import SafetyLimits

__all__ = ["DEFAULT_LIMITS_PATH", "LocalLimitsError", "load_local_limits", "tighten"]

#: Beside the stores. One file for the machine, not one per skill: the point is that it is
#: not the skill's to write.
DEFAULT_LIMITS_PATH = Path.home() / ".tendon" / "limits.yaml"


class LocalLimitsError(ValueError):
    """The local limits file exists and cannot be read.

    Raised rather than ignored. A malformed ceiling that silently did nothing would leave a
    site believing it had a bound it does not have, which is worse than having none.
    """


def load_local_limits(path: Path | None = None) -> SafetyLimits | None:
    """Read the machine's ceiling, or None when none is configured."""
    import yaml

    target = path if path is not None else DEFAULT_LIMITS_PATH
    if not target.is_file():
        return None

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LocalLimitsError(f"{target} could not be read: {exc}") from exc

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LocalLimitsError(f"{target} does not contain a mapping")

    block = raw.get("safety", raw)
    if not isinstance(block, dict):
        raise LocalLimitsError(f"{target} has a 'safety' key that is not a mapping")

    try:
        return SafetyLimits.model_validate(block)
    except Exception as exc:  # noqa: BLE001 - pydantic's own hierarchy
        raise LocalLimitsError(f"{target} is not a valid set of limits: {exc}") from exc


def tighten(skill: SafetyLimits, ceiling: SafetyLimits | None) -> SafetyLimits:
    """The stricter of the two, field by field.

    A limit set on only one side is used as-is: a ceiling that names a velocity and nothing
    else is a site with an opinion about velocity, not a site declaring everything else
    unbounded.

    Workspace bounds intersect rather than compare: the tighter box is the overlap, so a
    ceiling can shrink a skill's reach on any axis without having to restate the others.
    """
    if ceiling is None:
        return skill

    return SafetyLimits(
        max_joint_velocity=_smaller(skill.max_joint_velocity, ceiling.max_joint_velocity),
        max_force=_smaller(skill.max_force, ceiling.max_force),
        workspace_min=_inner(skill.workspace_min, ceiling.workspace_min, keep=max),
        workspace_max=_inner(skill.workspace_max, ceiling.workspace_max, keep=min),
    )


def _smaller(skill: float | None, ceiling: float | None) -> float | None:
    if skill is None:
        return ceiling
    if ceiling is None:
        return skill
    return min(skill, ceiling)


def _inner(skill, ceiling, *, keep):
    """The tighter side of a workspace bound, per axis.

    `keep` is `max` for the lower corner and `min` for the upper one — in both cases the
    value that shrinks the box. Axis counts that disagree are a body mismatch rather than a
    limits question, and the shorter one wins so the result is never wider than either.
    """
    if skill is None:
        return ceiling
    if ceiling is None:
        return skill
    return [keep(a, b) for a, b in zip(skill, ceiling, strict=False)]
