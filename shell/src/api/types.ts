/**
 * The shared vocabulary, restated for the shell.
 *
 * This file mirrors `src/tendon/kernel/types.py` by hand. Generating it would add a build
 * step to a project that has to stay easy to run, so the duplication is deliberate and the
 * cost is paid by `tests/unit/test_api_contract.py`, which fails when a field exists on
 * one side and not the other.
 *
 * A silent divergence here surfaces as an operator seeing stale confidence during an
 * intervention. That is the worst possible place to find it, which is why the test is not
 * optional.
 *
 * Datetimes arrive as ISO 8601 strings over JSON. Python tuples arrive as arrays.
 */

// --------------------------------------------------------------------------- capability

/** What the body can close on things with. NONE means it cannot grasp. */
export enum GripperKind {
  NONE = "none",
  PARALLEL = "parallel",
  SUCTION = "suction",
  MULTIFINGER = "multifinger",
}

/**
 * What a body can do, declared once at load time.
 *
 * The shell uses this to decide what to render: a body with no cameras gets no scene
 * view, and a read-only body gets no intervention controls at all.
 */
export interface Capability {
  body_id: string;
  /** Controllable arm axes, excluding the gripper — that is `gripper` plus
   *  `Action.gripper`. Counting the jaw here would double-count it. */
  dof: number;
  gripper: GripperKind;
  /** Rate the driver accepts setpoints at [Hz]. */
  control_hz: number;
  cameras: string[];
  has_force_sensing: boolean;
  /**
   * True when this body exists only in software.
   *
   * Defaults to false on the runtime side so a driver that does not say is treated as
   * real. The shell shows the difference prominently: an operator approving a motion
   * needs to know whether it happens in a window or in the room.
   */
  simulated: boolean;
  /** True for bodies that produce observations but accept no commands. */
  readonly: boolean;
}

// --------------------------------------------------------------------------- perception

/** What the body knows about itself. */
export interface Proprioception {
  /** [rad], or [m] for prismatic joints. */
  joint_positions: number[];
  /** [rad/s], or [m/s]. */
  joint_velocities: number[] | null;
  /** 0 closed, 1 open. */
  gripper_open: number | null;
  /** [N]. */
  force: number[] | null;
}

/** One timestep as seen by a policy. Frames are references, not image data. */
export interface Observation {
  /** ISO 8601. */
  t: string;
  step: number;
  proprio: Proprioception;
  /** camera name -> frame reference */
  frames: Record<string, string>;
  extra: Record<string, unknown>;
}

// --------------------------------------------------------------------------- action

/** How to read the numbers in an Action. */
export enum ActionSpace {
  JOINT_POSITION = "joint_position",
  JOINT_VELOCITY = "joint_velocity",
  EE_DELTA_POSE = "ee_delta_pose",
  EE_ABS_POSE = "ee_abs_pose",
}

/** A single commanded step. */
export interface Action {
  space: ActionSpace;
  values: number[];
  gripper: number | null;
}

/**
 * Where a confidence score came from.
 *
 * No upstream policy reports confidence — LeRobot, OpenVLA and GR00T all return a bare
 * action tensor — so every score here was produced by something tendon added, and which
 * something matters.
 *
 * The shell must distinguish NONE from a low score. They look identical as numbers and
 * are opposite in meaning: one says the policy is unsure, the other says nobody measured.
 * An operator who cannot tell them apart will misread the case where it matters.
 *
 * See docs/decisions/0003-confidence-has-no-upstream-source.md.
 */
export enum ConfidenceSource {
  /** No estimator. The score is not a measurement and must not be shown as one. */
  NONE = "none",
  /** Spread across sampled action chunks. Cheap, uncalibrated. */
  CHUNK_VARIANCE = "chunk_variance",
  /** Disagreement across policies or seeds. */
  ENSEMBLE = "ensemble",
  /** A head trained to predict its own success. v0.3 onward. */
  LEARNED_HEAD = "learned_head",
  /** Whether the observation resembles the training distribution. */
  OOD = "ood",
}

/**
 * How sure the policy is, why it might not be, and where the number came from.
 *
 * `reasons` is what the operator actually reads. A bare number does not help anyone
 * decide in two seconds, so the panel leads with the reasons and treats the score as
 * secondary.
 */
export interface Confidence {
  /** 0 to 1. Meaningless when `source` is NONE. */
  score: number;
  source: ConfidenceSource;
  reasons: string[];
}

/**
 * An action chunk plus everything a human needs to judge it.
 *
 * This is the unit of review — what `panels/IntentPreview` renders before the body moves.
 */
export interface Intent {
  /** ISO 8601. */
  issued_at: string;
  /** Wall-clock span this chunk covers [s]. */
  horizon_s: number;
  actions: Action[];
  confidence: Confidence;
  /** Natural language, for the operator. */
  goal: string | null;
  /** Object or site being acted on. */
  target: string | null;
}

// --------------------------------------------------------------------------- safety

/** Hard bounds enforced on every action, including operator corrections. */
export interface SafetyLimits {
  /** [rad/s]. */
  max_joint_velocity: number | null;
  /** [N]. */
  max_force: number | null;
  /** [m]. */
  workspace_min: number[] | null;
  /** [m]. */
  workspace_max: number[] | null;
}

/**
 * The result of a safety check. Three things, not two.
 *
 * `unchecked` names limits that could not be evaluated from the information available —
 * a joint-space command cannot be tested against a workspace without forward kinematics,
 * which the kernel deliberately does not have.
 *
 * The shell must show this. An operator told an action is "allowed" when a limit was
 * never evaluated has been told something false, and is the person who pays for it.
 */
export interface SafetyVerdict {
  allowed: boolean;
  violated: string[];
  /** Limits that could not be evaluated, and what was missing. */
  unchecked: string[];
  /** Present when the action was admissible after clamping. */
  clamped: Action | null;
}

// --------------------------------------------------------------------------- interrupt

export enum InterruptReason {
  LOW_CONFIDENCE = "low_confidence",
  SAFETY_TRIP = "safety_trip",
  OPERATOR_REQUEST = "operator_request",
  DRIVER_FAULT = "driver_fault",
}

export enum Resolution {
  APPROVED = "approved",
  REJECTED = "rejected",
  CORRECTED = "corrected",
  ABORTED = "aborted",
}

/**
 * Saved state that makes resume possible.
 *
 * The shell renders this frozen: the observation at the moment control was handed over,
 * not a live feed. An operator deciding against a moving picture is deciding about a
 * situation that has already passed.
 */
export interface InterruptContext {
  episode_id: string;
  step: number;
  reason: InterruptReason;
  intent: Intent;
  observation: Observation;
  /** ISO 8601. */
  raised_at: string;
}

/** What the operator decided. Recorded as training data, not just as a log line. */
export interface InterruptResolution {
  resolution: Resolution;
  correction: Intent | null;
  /** Operator words, for example: approach from the left. */
  note: string | null;
  /** ISO 8601. */
  resolved_at: string;
}

// --------------------------------------------------------------------------- episode

/** Sidecar record. The frames themselves are LeRobotDataset; see ADR 0001. */
export interface EpisodeMeta {
  episode_id: string;
  skill: string;
  body_id: string;
  /** ISO 8601. */
  started_at: string;
  ended_at: string | null;
  steps: number;
  interrupts: number;
  success: boolean | null;
  /** 0 to 1. */
  curation_score: number | null;
}
