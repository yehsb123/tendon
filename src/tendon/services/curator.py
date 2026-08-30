"""Which episodes are worth training on.

One of the four things tendon writes itself, and the hardest. Collecting is easy.
Deciding which of the 300 episodes recorded today should shape tomorrow policy is an
open problem that nobody has agreed on.

A metric here that mislabels good episodes as bad poisons training silently, and no
downstream test catches it. That is why this module gets the strictest tests in the
repository.

Starting signals for v0.1, all cheap and all falsifiable:

    jerk            large discontinuities in the commanded trajectory
    idle            long stretches with no meaningful motion
    length outlier  far from the median for this skill
    gripper churn   open/close toggled far more than the task needs
    instruction     language goal inconsistent with what the actions did

Interrupt episodes are scored separately and kept by default. They are the only
recorded instances of recovering from failure, which demonstration data almost never
contains.
"""

from __future__ import annotations


class Curator:
    def score(self, episode_id: str) -> float:
        """Score in [0, 1]. Higher means more worth training on."""
        raise NotImplementedError("v0.1")

    def select(self, skill: str, limit: int | None = None) -> list[str]:
        """Episode ids to train on, best first."""
        raise NotImplementedError("v0.1")
