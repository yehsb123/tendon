"""A policy that is uncertain in some situations and learns from corrections.

This is what closes the loop for v0.1. Everything else was in place — the scheduler raises
interrupts, safety checks every action, the recorder captures corrections, the evaluator
measures the rate — but nothing produced a confidence that could fall, and nothing turned a
correction into different behaviour. Without those two, the loop is a diagram.

## Why not LoRA here

`services/trainer.py` does LoRA, and that is the right answer once a real policy is being
run. It needs a GPU, a base model, and hours; none of which belong in a test or in the
example that demonstrates the loop closes.

This learns by remembering: a correction is stored against the situation it was given in,
and reused when a similar situation recurs. Instance-based, no gradients, no model. That is
a weaker learner than LoRA and an honest one — the claim being demonstrated is *the loop
closes*, not *this is how a robot should learn*.

The two are interchangeable behind `Policy`, which is the point of the protocol.

## Where the uncertainty comes from

`StochasticPolicy` wraps any deterministic function and perturbs it, with the perturbation
growing in regions the policy is told it is unsure about. Sampling it n times and measuring
the spread gives exactly what `confidence.estimate_from_samples` expects.

That is a simulated uncertainty, and it is labelled as such. A real policy's uncertainty
comes from its own stochasticity; this one is constructed so the interrupt path can be
exercised end to end without a model. Confusing the two would be the kind of demo that
proves nothing.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from tendon.kernel.types import (
    Action,
    ActionSpace,
    Confidence,
    ConfidenceSource,
    Intent,
    InterruptResolution,
    Observation,
)
from tendon.services.confidence import estimate_from_samples

__all__ = ["AdaptivePolicy", "CorrectionMemory", "StochasticPolicy", "UncertainRegion"]


@dataclass(frozen=True)
class UncertainRegion:
    """Where a policy is unsure, expressed in joint space.

    A stand-in for whatever makes a real policy uncertain — an unfamiliar object, an
    out-of-distribution view. Defined over joint positions because that is what a body
    reports without a camera.
    """

    #: Which joint the region is defined on.
    joint: int
    #: Centre of the uncertain region [rad].
    centre: float
    #: How wide it is [rad]. Uncertainty falls off with distance from the centre.
    width: float
    #: Peak perturbation applied at the centre [rad].
    magnitude: float

    def weight_at(self, position: float) -> float:
        """How uncertain this region makes the policy at that position, 0 to 1."""
        if self.width <= 0:
            return 0.0
        distance = abs(position - self.centre) / self.width
        return math.exp(-(distance**2))


class StochasticPolicy:
    """Wraps a deterministic trajectory function and makes it uncertain in places.

    Sampling is what produces a confidence: `predict` runs the underlying function several
    times with perturbation and measures the spread, exactly as
    `confidence.estimate_from_samples` expects. Where the body is outside every uncertain
    region the samples agree, the spread is near zero, and confidence is high.

    The returned chunk is the *mean* of the samples rather than one of them. A single
    sample would be a worse action than the policy is capable of, and the operator would be
    reviewing noise rather than intent.
    """

    def __init__(
        self,
        fn: Callable[[int], list[float]],
        *,
        control_hz: float,
        dof: int,
        regions: Sequence[UncertainRegion] = (),
        samples: int = 5,
        chunk_size: int = 10,
        reference_spread: float = 0.01,
        seed: int = 0,
        name: str = "stochastic",
    ) -> None:
        if samples < 3:
            raise ValueError(f"need at least 3 samples to measure spread, got {samples}")

        self._fn = fn
        self._control_hz = control_hz
        self._dof = dof
        self._regions = tuple(regions)
        self._samples = samples
        self._chunk_size = chunk_size
        self._reference_spread = reference_spread
        self._name = name
        self._seed = seed
        self._rng = random.Random(seed)
        self._step = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        return (ActionSpace.JOINT_POSITION,)

    def reset(self) -> None:
        self._step = 0
        # Reseeded so two episodes with the same seed produce the same run. Evaluation
        # compares runs, and an unseeded policy makes every comparison noise.
        self._rng = random.Random(self._seed)

    def uncertainty_at(self, observation: Observation) -> float:
        """How uncertain the policy is here, 0 to 1. Exposed for tests and diagnostics."""
        positions = observation.proprio.joint_positions
        weights = [
            region.weight_at(positions[region.joint])
            for region in self._regions
            if region.joint < len(positions)
        ]
        return max(weights, default=0.0)

    def predict(self, observation: Observation) -> Intent:
        weight = self.uncertainty_at(observation)

        chunks: list[list[Action]] = []
        for _ in range(self._samples):
            chunk = []
            for offset in range(self._chunk_size):
                base = self._fn(self._step + offset)
                perturbed = [
                    value + self._perturbation(weight, joint)
                    for joint, value in enumerate(base)
                ]
                chunk.append(Action(space=ActionSpace.JOINT_POSITION, values=perturbed))
            chunks.append(chunk)

        confidence = estimate_from_samples(
            chunks, reference_spread=self._reference_spread
        )

        # The mean of the samples, not one of them: a single draw is a worse action than
        # the policy can produce, and an operator would be reviewing noise.
        mean = [
            Action(
                space=ActionSpace.JOINT_POSITION,
                values=[
                    sum(chunk[i].values[j] for chunk in chunks) / len(chunks)
                    for j in range(self._dof)
                ],
            )
            for i in range(self._chunk_size)
        ]

        self._step += self._chunk_size
        return Intent(
            horizon_s=self._chunk_size / self._control_hz,
            actions=tuple(mean),
            confidence=confidence,
            goal=f"{self._name} step {self._step}",
        )

    def _perturbation(self, weight: float, joint: int) -> float:
        if weight <= 0.0:
            return 0.0
        scale = max(
            (r.magnitude * weight for r in self._regions if r.joint == joint), default=0.0
        )
        return self._rng.gauss(0.0, scale) if scale > 0 else 0.0


@dataclass
class CorrectionMemory:
    """Corrections, stored against the situation they were given in.

    Instance-based rather than gradient-based, so the loop can be demonstrated without a
    GPU. A correction is recalled when the body is close to where it was given.

    `radius` is in joint-space distance [rad]. Too small and nothing is ever recalled; too
    large and one correction is applied to situations it was never meant for — which would
    make the intervention rate fall for the wrong reason, and that is the failure mode this
    whole project is built to avoid measuring wrongly.
    """

    radius: float = 0.08
    entries: list[tuple[list[float], Intent]] = field(default_factory=list)

    def remember(self, observation: Observation, correction: Intent) -> None:
        self.entries.append((list(observation.proprio.joint_positions), correction))

    def recall(self, observation: Observation) -> Intent | None:
        """The nearest stored correction, if one is close enough."""
        if not self.entries:
            return None

        here = observation.proprio.joint_positions
        best: tuple[float, Intent] | None = None

        for positions, correction in self.entries:
            distance = math.sqrt(
                sum((a - b) ** 2 for a, b in zip(positions, here, strict=False))
            )
            if best is None or distance < best[0]:
                best = (distance, correction)

        if best is not None and best[0] <= self.radius:
            return best[1]
        return None

    def __len__(self) -> int:
        return len(self.entries)


class AdaptivePolicy:
    """A stochastic policy that reuses what an operator taught it.

    When the body is near a situation that was corrected before, the stored correction is
    returned with high confidence, and no interrupt is raised. That is the mechanism by
    which the intervention rate falls — and it falls only where a human actually
    intervened, which is what makes the resulting graph mean something.
    """

    def __init__(self, inner: StochasticPolicy, memory: CorrectionMemory | None = None) -> None:
        self._inner = inner
        self.memory = memory if memory is not None else CorrectionMemory()

    @property
    def name(self) -> str:
        return f"adaptive:{self._inner.name}"

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        return self._inner.requires

    def reset(self) -> None:
        self._inner.reset()

    def predict(self, observation: Observation) -> Intent:
        recalled = self.memory.recall(observation)
        if recalled is not None:
            # Confidence is high because the situation is one a human already resolved.
            # The source stays honest about where the number came from.
            return Intent(
                horizon_s=recalled.horizon_s,
                actions=recalled.actions,
                confidence=Confidence(
                    score=1.0,
                    source=ConfidenceSource.CHUNK_VARIANCE,
                    reasons=("recalled from an operator correction in this situation",),
                ),
                goal=recalled.goal,
                target=recalled.target,
            )
        return self._inner.predict(observation)

    def learn_from(self, observation: Observation, resolution: InterruptResolution) -> bool:
        """Store a correction. Returns whether anything was learned.

        Only `CORRECTED` teaches. An approval says the policy was right and there is
        nothing new; a rejection says it was wrong without saying what to do instead.
        Treating either as a lesson would move the intervention rate without any
        information having been added.
        """
        if resolution.correction is None:
            return False
        self.memory.remember(observation, resolution.correction)
        return True
