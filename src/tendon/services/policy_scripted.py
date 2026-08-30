"""A scripted policy, and why a project about learned policies ships one.

Three reasons, in order of how much they matter.

**It is the baseline v0.3 is measured against.** The roadmap says the project is proven or
discarded on one graph: cumulative human corrections against intervention rate. A falling
line means nothing without something to compare it to. A policy that always does the same
thing, correctly, is the fixed reference — if a fine-tuned SmolVLA cannot beat a hardcoded
sequence on the cube task, that is worth knowing before six weeks of training runs.

**It closes the loop with no model.** Driver, scheduler, recorder and policy can all be
exercised together on a laptop with no GPU, no download, and no weights. Every part of
design decision 1 becomes runnable in CI rather than aspirational.

**It tests the abstraction.** `kernel/protocols.Policy` claims the scheduler cannot tell a
VLA from a scripted controller from a replayed demonstration. This is the cheapest way to
find out whether that is true, and it was: nothing in the scheduler needed changing.

## On confidence

This policy reports `ConfidenceSource.NONE`, not high confidence.

It is deterministic. Running it twice on the same observation gives the same chunk, so
sample spread measures nothing — see `services/confidence.py`. Reporting 1.0 would make a
policy that never asks for help indistinguishable from one that is always right, which is
exactly the failure `ConfidenceSource` exists to prevent. It therefore cannot raise a
low-confidence interrupt, and that is correct: a scripted sequence has no opinion about
whether it is working.
"""

from __future__ import annotations

from collections.abc import Sequence

from tendon.kernel.types import (
    Action,
    ActionSpace,
    Confidence,
    ConfidenceSource,
    Intent,
    Observation,
)

# Joint targets [rad] for the cube task on an SO-ARM100, solved by damped least-squares IK
# against the measured grasp point and verified end to end by `benchmarks/capture_grasp.py`,
# which lifts the cube to 0.152 m against a 0.1 m success threshold.
#
# Hardcoded rather than solved here so this module needs no simulator: a policy that
# imported MuJoCo could not run against a replayed body or on a machine without it.
POSE_HOME = (0.0, -1.57, 1.57, 1.57, -1.57)
POSE_APPROACH = (0.042, -1.550, 1.543, 1.517, -1.567)  # 80mm above the cube
POSE_GRASP = (0.042, -1.164, 1.623, 1.250, -1.550)  # at the cube
POSE_LIFT = (0.042, -1.825, 1.392, 1.607, -1.572)  # 150mm above the cube

JAW_OPEN = 0.6  # ~58mm gap against a 30mm cube
JAW_SHUT = 0.0


class Stage:
    """One leg of the sequence: where to go, how open, and over how many steps."""

    __slots__ = ("pose", "jaw", "steps", "label")

    def __init__(self, pose: tuple[float, ...], jaw: float, steps: int, label: str) -> None:
        self.pose = pose
        self.jaw = jaw
        self.steps = steps
        self.label = label


#: The sequence `capture_grasp.py` renders. Step counts are control periods, so at 100Hz
#: the whole thing is 4.3 seconds.
CUBE_PICK: tuple[Stage, ...] = (
    Stage(POSE_APPROACH, JAW_OPEN, 80, "reach above the cube"),
    Stage(POSE_GRASP, JAW_OPEN, 80, "descend onto the cube"),
    Stage(POSE_GRASP, JAW_SHUT, 60, "close the gripper"),
    Stage(POSE_GRASP, JAW_SHUT, 30, "settle the grasp"),
    Stage(POSE_LIFT, JAW_SHUT, 120, "lift the cube"),
    Stage(POSE_LIFT, JAW_SHUT, 60, "hold"),
)


class ScriptedPolicy:
    """Plays a fixed sequence of joint targets, interpolating between them.

    Structurally a `kernel.protocols.Policy`. Emits one `Intent` per call covering
    `chunk_steps` control periods, so the scheduler sees exactly the shape a VLA produces:
    an action chunk with a horizon, not a single action.
    """

    def __init__(
        self,
        *,
        name: str = "scripted/cube-pick",
        task: str = "pick up the cube",
        stages: Sequence[Stage] = CUBE_PICK,
        control_hz: float = 100.0,
        chunk_steps: int = 50,
        start_pose: tuple[float, ...] = POSE_HOME,
        start_jaw: float = 0.3,
    ) -> None:
        """
        Args:
            name: Skill reference recorded on every episode.
            task: Instruction shown to an operator as the intent's goal.
            stages: The sequence to play.
            control_hz: The body's control rate [Hz], for the horizon.
            chunk_steps: Actions per `Intent`. Defaults to 50 to match SmolVLA's chunk
                size, so a scheduler tuned against one behaves the same against the other.
            start_pose: Where the body begins, used as the first interpolation origin. The
                scene's `start` keyframe.
            start_jaw: Gripper opening at that keyframe.
        """
        self._name = name
        self._task = task
        self._stages = tuple(stages)
        self._control_hz = float(control_hz)
        self._chunk_steps = int(chunk_steps)
        self._start_pose = tuple(start_pose)
        self._start_jaw = float(start_jaw)

        if self._control_hz <= 0:
            raise ValueError(f"control_hz must be positive, got {control_hz}")
        if self._chunk_steps < 1:
            raise ValueError(f"chunk_steps must be at least 1, got {chunk_steps}")

        self._plan: list[Action] = []
        self._cursor = 0
        self.reset()

    # ----------------------------------------------------------------- contract

    @property
    def name(self) -> str:
        return self._name

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        return (ActionSpace.JOINT_POSITION,)

    def reset(self) -> None:
        """Rebuild the plan and rewind to its start."""
        self._plan = self._build_plan()
        self._cursor = 0

    def predict(self, observation: Observation) -> Intent:
        """Return the next chunk of the plan.

        `observation` is ignored, which is the honest description of a scripted policy and
        the reason it is a baseline rather than a solution. It cannot recover from the cube
        being somewhere else.

        At the end of the plan the final action repeats. Ending an episode is the
        scheduler's decision, made on `max_steps` or a success condition; a policy that ran
        out of plan and started throwing would turn a finished task into a fault.
        """
        del observation

        if self._cursor >= len(self._plan):
            tail = self._plan[-1]
            chunk = [tail] * self._chunk_steps
        else:
            chunk = self._plan[self._cursor : self._cursor + self._chunk_steps]
            if len(chunk) < self._chunk_steps:
                chunk = chunk + [chunk[-1]] * (self._chunk_steps - len(chunk))
            self._cursor += self._chunk_steps

        return Intent(
            horizon_s=len(chunk) / self._control_hz,
            actions=tuple(chunk),
            confidence=Confidence(
                score=0.0,
                source=ConfidenceSource.NONE,
                reasons=(
                    "scripted policy: deterministic, so sample spread measures nothing "
                    "and confidence-based handover is disabled",
                ),
            ),
            goal=self._task,
            target=self._stage_label(),
        )

    # ---------------------------------------------------------------- internals

    def _stage_label(self) -> str | None:
        """Which leg of the sequence the next chunk starts in, for the shell."""
        consumed = 0
        for stage in self._stages:
            consumed += stage.steps
            if self._cursor <= consumed:
                return stage.label
        return self._stages[-1].label if self._stages else None

    def _build_plan(self) -> list[Action]:
        """Expand the stages into one action per control period.

        Linear interpolation in joint space. Not a trajectory optimiser and not trying to
        be: the poses are close enough together that a straight line between them stays
        inside the workspace, which `capture_grasp.py` demonstrates by lifting the cube.
        """
        plan: list[Action] = []
        pose = self._start_pose
        jaw = self._start_jaw

        for stage in self._stages:
            for i in range(stage.steps):
                t = (i + 1) / stage.steps
                blended = tuple(a * (1 - t) + b * t for a, b in zip(pose, stage.pose, strict=True))
                plan.append(
                    Action(
                        space=ActionSpace.JOINT_POSITION,
                        values=list(blended),  # [rad]
                        gripper=jaw * (1 - t) + stage.jaw * t,
                    )
                )
            pose = stage.pose
            jaw = stage.jaw

        return plan

    @property
    def plan_steps(self) -> int:
        """Total control periods the sequence covers, for sizing `max_steps`."""
        return len(self._plan)
