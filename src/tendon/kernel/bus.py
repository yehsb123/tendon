"""The action bus.

Policies publish intent, drivers subscribe. Neither holds a reference to the other,
which is what lets a body be swapped without touching policy code.

In-process asyncio for v0.1. The pub/sub shape is chosen now so that moving to a
distributed transport later (Zenoh, ROS 2, DDS) does not change any caller.
"""

from __future__ import annotations

from tendon.kernel.types import Intent, Observation


class ActionBus:
    """Single-writer, multi-reader channel for the current step.

    The recorder is a subscriber like any other. That is what makes design decision 1
    structural rather than a policy: recording is not a mode, it is a reader that is
    always attached.
    """

    async def publish_intent(self, intent: Intent) -> None:
        raise NotImplementedError("v0.1")

    async def publish_observation(self, obs: Observation) -> None:
        raise NotImplementedError("v0.1")
