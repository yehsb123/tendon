"""Loading a skill package, and checking it against the body it will run on.

Design decision 4. A skill is a policy plus its evaluation set, safety limits and required
capabilities — and the capabilities are the part that matters here, because they are what
makes a mismatch a load-time failure instead of a surprise mid-episode.

## Why compatibility is checked before anything moves

`skill.yaml` declares what the body must be able to do. A skill written for a parallel
gripper on a six-axis arm will not do anything sensible on a suction cup, and discovering
that at step 40 means a robot is already moving when the mismatch is found.

So `check_compatibility` runs at load time and reports **every** problem at once. Reporting
the first one and stopping would make configuring a new body a sequence of runs, each
revealing one more thing.

## What a skill does not do here

Resolve weights. `policy.base` names a Hub reference and this module does not fetch it —
that is `registry` (v0.4). A skill loads fine with no policy available, because the
baseline policies in `policies.py` are run against the same limits and thresholds, and
being able to run a scripted baseline on a skill definition without a model is exactly the
point of separating them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tendon.kernel.protocols import Driver
from tendon.kernel.types import ActionSpace, Capability, GripperKind, SafetyLimits

__all__ = [
    "IncompatibleBody",
    "Requirements",
    "Skill",
    "SkillError",
    "check_compatibility",
    "load_skill",
]

_SUPPORTED_API = "tendon/v1alpha1"


class SkillError(ValueError):
    """A skill file is malformed or unsupported."""


class IncompatibleBody(SkillError):
    """A skill cannot run on the body it was pointed at.

    Raised at load time, never during an episode. Carries every reason rather than the
    first, since configuring a new body should not be a sequence of runs.
    """

    def __init__(self, skill: str, body_id: str, reasons: tuple[str, ...]) -> None:
        self.skill = skill
        self.body_id = body_id
        self.reasons = reasons
        detail = "\n  ".join(reasons)
        super().__init__(f"{skill} cannot run on {body_id}:\n  {detail}")


@dataclass(frozen=True)
class Requirements:
    """What the body must be able to do."""

    dof: int | None = None
    gripper: GripperKind | None = None
    action_spaces: tuple[ActionSpace, ...] = ()
    cameras: tuple[str, ...] = ()
    control_hz: float | None = None


@dataclass(frozen=True)
class Skill:
    namespace: str
    name: str
    version: str
    summary: str = ""
    license: str = ""
    requires: Requirements = field(default_factory=Requirements)
    limits: SafetyLimits = field(default_factory=SafetyLimits)
    #: Below this confidence, hand over. A starting point rather than a recommendation —
    #: confidence is not calibrated across skills until v0.3 (ADR 0003).
    confidence_threshold: float = 0.5
    #: Hub reference for the base policy. Not resolved here.
    policy_base: str | None = None
    policy_adapter: str | None = None
    eval_episodes: int = 50
    #: Success conditions, checked against `Observation.extra` at the end of an episode.
    #: The body supplies the quantity; the skill names it. Neither knows about the other.
    success_criteria: tuple[tuple[str, float], ...] = ()
    source: Path | None = None

    @property
    def ref(self) -> str:
        return f"{self.namespace}/{self.name}"


#: Where skills live when a reference is given instead of a path. The API resolves
#: `namespace/name` under the same directory; a reference that works in the shell and
#: fails on the command line is the kind of difference nobody can be expected to hold.
SKILL_ROOT = Path("skills")


def _resolve(path: str | Path) -> Path:
    """Find a `skill.yaml` from either a path or a `namespace/name` reference.

    A path is tried first and wins, so nothing that used to work changes. The reference
    form exists because it is what everything else in the project calls a skill: the API
    serves `/api/skills/{namespace}/{name}`, the shell lists `grasp/cube-sim`, the run
    output prints `grasp/cube-sim`, and the README documents `tendon run <skill>`. Only
    the command line required `skills/grasp/cube-sim`, and typing the documented form got
    `no skill file at grasp\\cube-sim` — an error about a path, for someone who was not
    thinking about paths.
    """
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "skill.yaml"
    if candidate.exists():
        return candidate

    # Only a bare `namespace/name` is worth retrying. Anything with a suffix or more
    # segments was meant as a path, and reporting the path the caller typed is more
    # useful than reporting somewhere they never mentioned.
    parts = Path(path).parts
    if len(parts) == 2 and not Path(path).suffix:
        under_root = SKILL_ROOT / parts[0] / parts[1] / "skill.yaml"
        if under_root.exists():
            return under_root
        raise SkillError(
            f"no skill file at {candidate}, and no skill {parts[0]}/{parts[1]} "
            f"under {SKILL_ROOT}{os.sep}"
        )

    raise SkillError(f"no skill file at {candidate}")


def load_skill(path: str | Path) -> Skill:
    """Read and validate a `skill.yaml`.

    Validation is strict about structure and permissive about extra keys: a skill written
    for a later version of tendon should still load, and an unknown key is far more likely
    to be a feature we have not implemented than a typo worth refusing over.

    A misspelled *known* key is the dangerous case — `max_joint_velocty` would silently
    leave a limit unset — so the safety block is checked for near-misses explicitly.
    """
    path = _resolve(path)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SkillError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise SkillError(f"{path} does not contain a mapping")

    api = raw.get("apiVersion")
    if api != _SUPPORTED_API:
        raise SkillError(
            f"{path} declares apiVersion {api!r}; this tendon understands {_SUPPORTED_API!r}"
        )
    if raw.get("kind") != "Skill":
        raise SkillError(f"{path} is kind {raw.get('kind')!r}, expected 'Skill'")

    metadata = _mapping(raw, "metadata", path)
    for required in ("name", "namespace", "version"):
        if not metadata.get(required):
            raise SkillError(f"{path} metadata is missing {required!r}")

    return Skill(
        namespace=str(metadata["namespace"]),
        name=str(metadata["name"]),
        version=str(metadata["version"]),
        summary=str(metadata.get("summary", "")),
        license=str(metadata.get("license", "")),
        requires=_requirements(_mapping(raw, "requires", path, optional=True), path),
        limits=_limits(_mapping(raw, "safety", path, optional=True), path),
        confidence_threshold=_threshold(_mapping(raw, "interrupt", path, optional=True), path),
        policy_base=_optional_str(_mapping(raw, "policy", path, optional=True).get("base")),
        policy_adapter=_optional_str(_mapping(raw, "policy", path, optional=True).get("adapter")),
        eval_episodes=int(_mapping(raw, "eval", path, optional=True).get("episodes", 50)),
        success_criteria=_success(_mapping(raw, "eval", path, optional=True), path),
        source=path,
    )


def check_compatibility(skill: Skill, driver: Driver) -> tuple[str, ...]:
    """Every reason this skill cannot run on this body. Empty means it can.

    Returns rather than raises so a caller can report all of them, or decide that a
    particular mismatch is acceptable — a skill needing 50Hz on a body that runs at 200Hz
    is fine, and this says so by not complaining.
    """
    capability: Capability = driver.capability
    required = skill.requires
    reasons: list[str] = []

    if required.dof is not None and capability.dof < required.dof:
        reasons.append(
            f"needs {required.dof} controllable degrees of freedom, body has {capability.dof}"
        )

    if required.gripper is not None and capability.gripper is not required.gripper:
        reasons.append(
            f"needs a {required.gripper.value} gripper, body has {capability.gripper.value}"
        )

    if required.action_spaces:
        accepted = set(driver.accepts)
        if not accepted.intersection(required.action_spaces):
            reasons.append(
                f"needs one of {[s.value for s in required.action_spaces]}, "
                f"body accepts {[s.value for s in driver.accepts]}"
            )

    missing_cameras = [c for c in required.cameras if c not in capability.cameras]
    if missing_cameras:
        reasons.append(f"needs camera(s) {missing_cameras}, body has {list(capability.cameras)}")

    if required.control_hz is not None and capability.control_hz < required.control_hz:
        # Faster is fine; slower is not. A skill recorded at 50Hz replayed at 20Hz plays
        # in slow motion, which is a different trajectory wearing the same numbers.
        reasons.append(
            f"needs {required.control_hz:g} Hz control, body runs at {capability.control_hz:g} Hz"
        )

    if capability.readonly:
        reasons.append("body is read-only and accepts no commands")

    return tuple(reasons)


def require_compatible(skill: Skill, driver: Driver) -> None:
    """Raise unless the skill can run on this body."""
    reasons = check_compatibility(skill, driver)
    if reasons:
        raise IncompatibleBody(skill.ref, driver.capability.body_id, reasons)


# --------------------------------------------------------------------------- internals


def _mapping(raw: dict, key: str, path: Path, *, optional: bool = False) -> dict[str, Any]:
    value = raw.get(key)
    if value is None:
        if optional:
            return {}
        raise SkillError(f"{path} is missing the {key!r} block")
    if not isinstance(value, dict):
        raise SkillError(f"{path}: {key!r} must be a mapping, got {type(value).__name__}")
    return value


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _requirements(block: dict[str, Any], path: Path) -> Requirements:
    gripper = block.get("gripper")
    spaces = block.get("action_spaces") or []

    try:
        return Requirements(
            dof=int(block["dof"]) if "dof" in block else None,
            gripper=GripperKind(gripper) if gripper is not None else None,
            action_spaces=tuple(ActionSpace(s) for s in spaces),
            cameras=tuple(str(c) for c in (block.get("cameras") or [])),
            control_hz=float(block["control_hz"]) if "control_hz" in block else None,
        )
    except ValueError as exc:
        raise SkillError(f"{path}: invalid requires block: {exc}") from exc


#: Known keys in the safety block. A misspelling here silently leaves a limit unset, which
#: is the one kind of typo worth refusing over.
_SAFETY_KEYS = {"max_joint_velocity", "max_force", "workspace_min", "workspace_max"}


def _limits(block: dict[str, Any], path: Path) -> SafetyLimits:
    unknown = set(block) - _SAFETY_KEYS
    if unknown:
        raise SkillError(
            f"{path}: unknown key(s) in the safety block: {sorted(unknown)}. "
            f"Known keys are {sorted(_SAFETY_KEYS)}. A misspelled limit is not enforced, "
            "and nothing downstream would report it as missing."
        )

    try:
        return SafetyLimits(
            max_joint_velocity=block.get("max_joint_velocity"),
            max_force=block.get("max_force"),
            workspace_min=block.get("workspace_min"),
            workspace_max=block.get("workspace_max"),
        )
    except ValueError as exc:
        raise SkillError(f"{path}: invalid safety block: {exc}") from exc


def _threshold(block: dict[str, Any], path: Path) -> float:
    value = block.get("confidence_threshold", 0.5)
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise SkillError(f"{path}: confidence_threshold must be a number, got {value!r}") from exc

    if not 0.0 <= threshold <= 1.0:
        raise SkillError(f"{path}: confidence_threshold must be between 0 and 1, got {threshold}")
    return threshold


def _success(block: dict[str, Any], path: Path) -> tuple[tuple[str, float], ...]:
    """Parse `eval.success` into (name, threshold) pairs.

    Left as raw pairs rather than parsed criteria so that `skill.py` does not depend on
    `evaluator.py` — a skill is a description, and how success is judged belongs with the
    thing that judges it.
    """
    success = block.get("success")
    if success is None:
        return ()
    if not isinstance(success, dict):
        raise SkillError(f"{path}: eval.success must be a mapping of condition to threshold")

    pairs: list[tuple[str, float]] = []
    for name, threshold in success.items():
        try:
            pairs.append((str(name), float(threshold)))
        except (TypeError, ValueError) as exc:
            raise SkillError(
                f"{path}: eval.success[{name!r}] must be a number, got {threshold!r}"
            ) from exc
    return tuple(pairs)
