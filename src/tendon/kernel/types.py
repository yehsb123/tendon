"""The vocabulary of tendon.

Every layer speaks these types. They are deliberately small: a type here becomes a
constraint on every driver, service and shell view, so anything not needed by more
than one layer belongs somewhere else.

Design decision 3 lives in this file. A policy emits `Intent` without naming a body;
a driver turns `Intent` into whatever its body requires.

## Units

**SI, radians, seconds. A driver converts; the kernel never does.**

- joint position: [rad] for a revolute axis, [m] for a prismatic one
- joint velocity: [rad/s] or [m/s], matching the axis
- translation, workspace bounds: [m]
- rotation: [rad]
- force: [N]; torque: [N.m]
- gripper: [normalised], 0 closed to 1 open — never jaw width
- rates: [Hz]

Stated here because it was stated nowhere. `kernel/safety` compares a skill's declared
limit against what a driver reports, and both are bare floats: if the two disagree about
units the comparison still succeeds and means nothing. An arm reporting degrees makes
every limit wrong by 57, in the permissive direction, and nothing in this repository could
have noticed — the numbers arrive and they are numbers.

Every field carrying a physical quantity says its unit in its own `description`, so the
unit travels into the JSON schema the API and the shell are generated from, rather than
living in a comment beside one example skill. `test_units_are_declared.py` fails on a
numeric field that does not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

Vector = Annotated[list[float], Field(description="Dense float vector, driver-defined order")]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- capability


class GripperKind(str, Enum):
    """What the body can close on things with. NONE means it cannot grasp."""

    NONE = "none"
    PARALLEL = "parallel"
    SUCTION = "suction"
    MULTIFINGER = "multifinger"


class Capability(BaseModel):
    """What a body can do, declared once at load time.

    Negotiated when a skill is loaded, never consulted inside the control loop. If a
    policy has to branch on capability mid-episode, the abstraction has failed.
    """

    model_config = ConfigDict(frozen=True)

    body_id: str = Field(description="Stable identifier, e.g. mujoco:so101 or so101:tty0")
    dof: int = Field(
        gt=0,
        description=(
            "Controllable arm axes, excluding the gripper. The gripper is described by "
            "`gripper` and commanded through `Action.gripper`, so counting it here would "
            "double-count it and let a skill needing six arm axes match a five-joint arm "
            "that happens to have a jaw."
        ),
    )
    gripper: GripperKind = GripperKind.NONE
    control_hz: float = Field(gt=0, description="[Hz] Rate the driver accepts setpoints at")
    cameras: tuple[str, ...] = ()
    has_force_sensing: bool = False
    simulated: bool = Field(
        default=False,
        description=(
            "True when this body exists only in software. Defaults to False so that a "
            "driver which does not say is treated as real: the cost of asking about a "
            "simulator is a keystroke, and the cost of assuming a real arm is a "
            "simulator is a real arm moving."
        ),
    )
    readonly: bool = Field(
        default=False,
        description=(
            "True for bodies that produce observations but accept no commands, "
            "such as the human demonstration driver."
        ),
    )


# --------------------------------------------------------------------------- perception


class Proprioception(BaseModel):
    """What the body knows about itself.

    Units are part of the contract, not a convention — see the module docstring. A driver
    reporting degrees here makes every safety limit wrong by a factor of 57, silently, and
    `kernel/safety` has no way to notice: it compares numbers, and the numbers arrive.
    """

    joint_positions: Vector = Field(
        description="Per joint: [rad] for revolute axes, [m] for prismatic ones."
    )
    joint_velocities: Vector | None = Field(default=None, description="[rad/s] or [m/s]")
    gripper_open: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="[normalised] 0 closed, 1 open. Jaw width in metres belongs to the driver.",
    )
    force: Vector | None = Field(default=None, description="[N], or [N.m] for a torque axis")


class Observation(BaseModel):
    """One timestep as seen by a policy.

    Images are carried by reference, not by value: an observation crosses process and
    network boundaries many times per second, and the recorder writes frames to video
    rather than embedding them here.
    """

    t: datetime = Field(default_factory=_now)
    step: int = Field(ge=0)
    proprio: Proprioception
    frames: dict[str, str] = Field(
        default_factory=dict, description="camera name -> frame reference"
    )
    extra: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- action


class ActionSpace(str, Enum):
    """How to read the numbers in an Action. Drivers declare what they accept.

    Which quantity, not which unit. The unit for each is fixed by the module docstring:
    `JOINT_POSITION` is [rad] or [m] per joint, `JOINT_VELOCITY` is [rad/s] or [m/s], and
    both pose spaces are [m] for translation and [rad] for rotation.
    """

    JOINT_POSITION = "joint_position"
    JOINT_VELOCITY = "joint_velocity"
    EE_DELTA_POSE = "ee_delta_pose"
    EE_ABS_POSE = "ee_abs_pose"


class Action(BaseModel):
    """A single commanded step."""

    model_config = ConfigDict(frozen=True)

    space: ActionSpace
    values: Vector = Field(
        description=(
            "Read according to `space`: [rad] or [m] per joint for the joint spaces, "
            "[m] and [rad] for the pose spaces."
        )
    )
    gripper: float | None = Field(
        default=None, ge=0.0, le=1.0, description="[normalised] 0 closed, 1 open"
    )


class ConfidenceSource(str, Enum):
    """Where a confidence score came from.

    Carried because no upstream policy reports confidence at all — LeRobot, OpenVLA and
    GR00T all return a bare action tensor — so every score in this system is produced by
    something tendon added. Which something matters: a rate measured under chunk variance
    is not comparable to one measured under a learned head.

    See `docs/decisions/0003-confidence-has-no-upstream-source.md`.
    """

    #: No estimator. The score is not a measurement and must not be read as one.
    NONE = "none"
    #: Spread across sampled action chunks. Cheap, uncalibrated, and blind to a policy
    #: that is confidently wrong.
    CHUNK_VARIANCE = "chunk_variance"
    #: Disagreement across policies or seeds.
    ENSEMBLE = "ensemble"
    #: A head trained to predict its own success. Needs the labelled data the loop
    #: produces, so it is available from v0.3 onward.
    LEARNED_HEAD = "learned_head"
    #: Whether the observation resembles the training distribution.
    OOD = "ood"


class Confidence(BaseModel):
    """How sure the policy is, why it might not be, and where the number came from.

    `reasons` is what the shell leads with. A bare number does not help anyone decide in
    two seconds, and the reasons are what an operator can actually act on.

    `source` defaults to `NONE`, so a policy that says nothing about confidence produces a
    score that cannot raise an interrupt. That is deliberate: defaulting to a usable value
    would make a robot that never asks for help indistinguishable from one that is always
    right.
    """

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0.0, le=1.0)
    source: ConfidenceSource = Field(
        default=ConfidenceSource.NONE,
        description="Where the score came from; NONE means it is not a measurement",
    )
    reasons: tuple[str, ...] = ()

    @property
    def is_measured(self) -> bool:
        """Whether this score reflects an actual estimate."""
        return self.source is not ConfidenceSource.NONE


class Intent(BaseModel):
    """An action chunk plus everything a human needs to judge it.

    This is the unit of review. It exists because a large model cannot meet a control
    deadline and therefore plans ahead (see docs/architecture.md, "Two clocks"). The
    shell gets something to render before the body moves.
    """

    model_config = ConfigDict(frozen=True)

    issued_at: datetime = Field(default_factory=_now)
    horizon_s: float = Field(gt=0, description="[s] Wall-clock span this chunk covers")
    actions: tuple[Action, ...] = Field(min_length=1)
    confidence: Confidence
    goal: str | None = Field(default=None, description="Natural language, for the operator")
    target: str | None = Field(default=None, description="Object or site being acted on")


# --------------------------------------------------------------------------- safety


class SafetyLimits(BaseModel):
    """Hard bounds enforced on every action, independent of the policy.

    Checked after an operator correction as well: a human may correct a policy, but may
    not exceed a limit through the shell.

    These are the numbers a person writes in `skill.yaml`, and they are compared directly
    against what a driver reports. If the two disagree about units the comparison still
    succeeds and means nothing — which is why the unit is stated on the field rather than
    in a comment beside one example skill.
    """

    model_config = ConfigDict(frozen=True)

    max_joint_velocity: float | None = Field(default=None, gt=0, description="[rad/s] or [m/s]")
    max_force: float | None = Field(default=None, gt=0, description="[N]")
    workspace_min: Vector | None = Field(default=None, description="[m], in the body's frame")
    workspace_max: Vector | None = Field(default=None, description="[m], in the body's frame")


class SafetyVerdict(BaseModel):
    """The result of a safety check.

    Carries three things, not two. `unchecked` names the limits that could not be
    evaluated from the information available — a joint-space command cannot be tested
    against a workspace without forward kinematics, which the kernel deliberately does
    not have.

    A caller that ignores `unchecked` is choosing to proceed unverified. That is
    sometimes correct, but it should be a decision rather than an assumption, which is
    why an unevaluated limit is reported instead of quietly passing.
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool
    violated: tuple[str, ...] = ()
    unchecked: tuple[str, ...] = Field(
        default=(),
        description="Limits that could not be evaluated, and what was missing",
    )
    clamped: Action | None = Field(
        default=None, description="Present when the action was admissible after clamping"
    )


# --------------------------------------------------------------------------- interrupt


class InterruptReason(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    SAFETY_TRIP = "safety_trip"
    OPERATOR_REQUEST = "operator_request"
    DRIVER_FAULT = "driver_fault"


class Resolution(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTED = "corrected"
    ABORTED = "aborted"


class InterruptContext(BaseModel):
    """Saved state that makes resume possible.

    Design decision 2 stands or falls here. If this does not carry enough to resume,
    the event is a fault rather than an interrupt, and must be reported as one.
    """

    model_config = ConfigDict(frozen=True)

    episode_id: str
    step: int = Field(ge=0)
    reason: InterruptReason
    intent: Intent
    observation: Observation
    raised_at: datetime = Field(default_factory=_now)


class InterruptResolution(BaseModel):
    """What the operator decided. Recorded as training data, not just as a log line."""

    model_config = ConfigDict(frozen=True)

    resolution: Resolution
    correction: Intent | None = None
    note: str | None = Field(
        default=None,
        description="Operator words, for example: approach from the left",
    )
    resolved_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- episode


class EpisodeMeta(BaseModel):
    """Sidecar record. The frames themselves are LeRobotDataset; see ADR 0001."""

    episode_id: str
    skill: str
    body_id: str
    started_at: datetime = Field(default_factory=_now)
    ended_at: datetime | None = None
    steps: int = 0
    interrupts: int = 0
    success: bool | None = None
    curation_score: float | None = Field(default=None, ge=0.0, le=1.0)


__all__ = [
    "Action",
    "ActionSpace",
    "Capability",
    "Confidence",
    "ConfidenceSource",
    "EpisodeMeta",
    "GripperKind",
    "Intent",
    "InterruptContext",
    "InterruptReason",
    "InterruptResolution",
    "Observation",
    "Proprioception",
    "Resolution",
    "SafetyLimits",
    "SafetyVerdict",
]
