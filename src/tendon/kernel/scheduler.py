"""Two clocks.

A large model cannot meet a control deadline, so deliberation and control run at
different rates:

    deliberation  ~1-10Hz    policy produces an action chunk (Intent)
    control       100Hz+     driver-side interpolation to setpoints

The scheduler owns the boundary. It asks the policy for intent, checks each action
through safety, feeds the control tier, and raises interrupts when confidence drops.

This split is also what makes the shell possible: the action chunk is the artifact an
operator reviews, and it exists because of a latency constraint rather than for the
interface. See docs/architecture.md.
"""

from __future__ import annotations

from tendon.kernel.protocols import Driver
from tendon.kernel.types import SafetyLimits


class Scheduler:
    """Runs one skill on one body until the episode ends."""

    def __init__(self, driver: Driver, limits: SafetyLimits) -> None:
        self._driver = driver
        self._limits = limits

    async def run_episode(self, skill: str, max_steps: int | None = None) -> str:
        """Execute one episode and return its episode_id.

        Every step is published to the bus, so the recorder captures it without the
        scheduler knowing the recorder exists.
        """
        raise NotImplementedError("v0.1")
