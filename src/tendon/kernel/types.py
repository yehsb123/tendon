"""The vocabulary of tendon.

Every layer speaks these types. They are deliberately small: a type here becomes a
constraint on every driver, service and shell view, so anything not needed by more
than one layer belongs somewhere else.

Design decision 3 lives in this file. A policy emits `Intent` without naming a body;
a driver turns `Intent` into whatever its body requires.
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
    dof: int = Field(gt=0, description="Controllable degrees of freedom")
    gripper: GripperKind = GripperKind.NONE
    control_hz: float = Field(gt=0, description="Rate the driver accepts setpoints at")
    cameras: tuple[str, ...] = ()
    has_force_sensing: bool = False
    readonly: bool = Field(
        default=False,
        description=(
            "True for bodies that produce observations but accept no commands, "
            "such as the human demonstration driver."
        ),
    )


# --------------------------------------------------------------------------- perception


class Proprioception(BaseModel):
    """What the body knows about itself."""

    joint_positions: Vector
    joint_velocities: Vector | None = None
    gripper_open: float | None = Field(default=None, ge=0.0, le=1.0)
    force: Vector | None = None


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
    """How to read the numbers in an Action. Drivers declare what they accept."""

    JOINT_POSITION = "joint_position"
    JOINT_VELOCITY = "joint_velocity"
    EE_DELTA_POSE = "ee_delta_pose"
    EE_ABS_POSE = "ee_abs_pose"


class Action(BaseModel):
    """A single commanded step."""

    model_config = ConfigDict(frozen=True)

    space: ActionSpace
    values: Vector
    gripper: float | None = Field(default=None, ge=0.0, le=1.0)


class Confidence(BaseModel):
    """How sure the policy is, and why it might not be.

    `score` is what raises an interrupt. `reasons` is what the shell shows an operator,
    because a bare number does not help anyone decide in two seconds.
    """

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0.0, le=1.0)
    reasons: tuple[str, ...] = ()


class Intent(BaseModel):
    """An action chunk plus everything a human needs to judge it.

    This is the unit of review. It exists because a large model cannot meet a control
    deadline and therefore plans ahead (see docs/architecture.md, "Two clocks"). The
    shell gets something to render before the body moves.
    """

    model_config = ConfigDict(frozen=True)

    issued_at: datetime = Field(default_factory=_now)
    horizon_s: float = Field(gt=0, description="Wall-clock span this chunk covers")
    actions: tuple[Action, ...] = Field(min_length=1)
    confidence: Confidence
    goal: str | None = Field(default=None, description="Natural language, for the operator")
    target: str | None = Field(default=None, description="Object or site being acted on")


# --------------------------------------------------------------------------- safety


class SafetyLimits(BaseModel):
    """Hard bounds enforced on every action, independent of the policy.

    Checked after an operator correction as well: a human may correct a policy, but may
    not exceed a limit through the shell.
    """

    model_config = ConfigDict(frozen=True)

    max_joint_velocity: float | None = Field(default=None, gt=0)
    max_force: float | None = Field(default=None, gt=0)
    workspace_min: Vector | None = None
    workspace_max: Vector | None = None


class SafetyVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    violated: tuple[str, ...] = ()
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
