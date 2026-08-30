"""Policies that need no model.

Two of them, and neither is a toy. They exist because `Policy` has to be an abstraction
rather than an alias for a neural network, and the way to keep an abstraction honest is to
have implementations that are nothing like each other.

**`ReplayPolicy`** plays a recorded trajectory back. It is the fixed baseline every
evaluation needs: a run whose behaviour cannot drift, against which a learned policy is
compared. It is also how a human demonstration becomes a policy — the same episode that a
`human` driver produced can be handed to the scheduler and executed on a robot.

**`ScriptedPolicy`** generates a trajectory from a function of time. Used for smoke tests,
for exercising the interrupt path without a model, and for the `01_record` overhead
measurement, where the point is the loop cost rather than the behaviour.

## On confidence

Both report `ConfidenceSource.NONE`. A scripted policy has no uncertainty to estimate and
a replayed one has none either — the trajectory is what it is. Reporting anything else
would let a baseline appear to raise its own hand, which is precisely the capability
ADR 0004 says separates tendon from what already exists. A baseline that fakes it makes
the comparison meaningless.

Consequence: an evaluation of these policies has `is_comparable == False`, and the
evaluator says so. That is correct. They are baselines for success rate, not for
intervention rate.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from tendon.kernel.protocols import PolicyExhausted
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Confidence,
    ConfidenceSource,
    Intent,
    Observation,
)

__all__ = ["PolicyExhausted", "ReplayPolicy", "ScriptedPolicy"]

_NO_CONFIDENCE = Confidence(
    score=0.0,
    source=ConfidenceSource.NONE,
    reasons=("policy reports no confidence estimate",),
)


class ReplayPolicy:
    """Plays a recorded trajectory back, one chunk at a time.

    The fixed baseline for evaluation, and the path by which a human demonstration
    executes on a robot.

    Chunks are cut from the recording rather than predicted, so `horizon_s` is derived
    from the control rate the recording was made at. Passing a different rate would make
    the shell render a trajectory over the wrong span, which matters because an operator
    judges partly by how fast something is about to happen.
    """

    def __init__(
        self,
        actions: Sequence[Action],
        *,
        control_hz: float,
        chunk_size: int = 10,
        name: str = "replay",
        loop: bool = False,
    ) -> None:
        if not actions:
            raise ValueError("a replay policy needs at least one action")
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be at least 1, got {chunk_size}")
        if control_hz <= 0:
            raise ValueError(f"control_hz must be positive, got {control_hz}")

        self._actions = list(actions)
        self._chunk_size = chunk_size
        self._control_hz = control_hz
        self._name = name
        self._loop = loop
        self._cursor = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        """Whatever the recording used.

        Read from the actions rather than declared, since a recording cannot be replayed
        into a different action space without inventing the conversion.
        """
        seen: list[ActionSpace] = []
        for action in self._actions:
            if action.space not in seen:
                seen.append(action.space)
        return tuple(seen)

    @property
    def remaining(self) -> int:
        return max(0, len(self._actions) - self._cursor)

    def reset(self) -> None:
        self._cursor = 0

    def predict(self, observation: Observation) -> Intent:
        """Return the next chunk of the recording.

        The observation is ignored, which is the entire point: a baseline that reacted to
        the world would not be fixed, and a moving baseline cannot anchor a comparison.
        """
        if self._cursor >= len(self._actions):
            if not self._loop:
                raise PolicyExhausted(
                    f"replay {self._name!r} finished after {len(self._actions)} actions"
                )
            self._cursor = 0

        chunk = self._actions[self._cursor : self._cursor + self._chunk_size]
        self._cursor += len(chunk)

        return Intent(
            horizon_s=len(chunk) / self._control_hz,
            actions=tuple(chunk),
            confidence=_NO_CONFIDENCE,
            goal=f"replay {self._name}",
        )


class ScriptedPolicy:
    """Generates a trajectory from a function of step index.

    For smoke tests, for exercising the interrupt and safety paths without a model, and
    for the `01_record` overhead measurement where the loop cost is the subject and the
    behaviour is irrelevant.
    """

    def __init__(
        self,
        fn: Callable[[int], list[float]],
        *,
        control_hz: float,
        dof: int,
        chunk_size: int = 10,
        space: ActionSpace = ActionSpace.JOINT_POSITION,
        name: str = "scripted",
    ) -> None:
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be at least 1, got {chunk_size}")
        if control_hz <= 0:
            raise ValueError(f"control_hz must be positive, got {control_hz}")

        self._fn = fn
        self._control_hz = control_hz
        self._dof = dof
        self._chunk_size = chunk_size
        self._space = space
        self._name = name
        self._step = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        return (self._space,)

    def reset(self) -> None:
        self._step = 0

    def predict(self, observation: Observation) -> Intent:
        actions = []
        for offset in range(self._chunk_size):
            values = self._fn(self._step + offset)
            if len(values) != self._dof:
                raise ValueError(
                    f"scripted policy produced {len(values)} values for a {self._dof}-dof "
                    "body; a mismatched action would be clipped by the driver and "
                    "recorded as though it were intended"
                )
            actions.append(Action(space=self._space, values=list(values)))

        self._step += self._chunk_size
        return Intent(
            horizon_s=self._chunk_size / self._control_hz,
            actions=tuple(actions),
            confidence=_NO_CONFIDENCE,
            goal=f"scripted {self._name}",
        )


def sine_sweep(
    *, dof: int, amplitude: float = 0.2, period_steps: int = 200
) -> Callable[[int], list[float]]:
    """A gentle sweep on the first joint, the rest held at zero.

    Amplitude defaults small enough to be safe inside any workspace worth configuring,
    because the obvious way to misuse a scripted policy is to point it at real hardware
    with a number chosen for a simulator.
    """
    if dof < 1:
        raise ValueError(f"dof must be at least 1, got {dof}")

    def fn(step: int) -> list[float]:
        value = amplitude * math.sin(2 * math.pi * step / period_steps)
        return [value] + [0.0] * (dof - 1)

    return fn
