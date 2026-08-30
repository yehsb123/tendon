"""The interrupt protocol — design decision 2.

An E-stop cuts power: context is destroyed, nothing is recorded, nothing is learned.
An interrupt saves enough state to resume, hands control to a human, and records what
the human did as training data.

State machine:

    RUNNING --raise--> PENDING --resolve--> RESUMING --> RUNNING
                          |
                          +--abort--> STOPPED

The invariant that matters: if the saved context is not sufficient to resume, the event
is a fault, not an interrupt, and must be reported as a fault. Silently degrading an
interrupt into a stop would make the intervention rate look better than it is, which is
the one metric this project is judged on.
"""

from __future__ import annotations

from tendon.kernel.types import InterruptContext, InterruptReason, InterruptResolution


class InterruptController:
    """Raises, holds and resolves interrupts for one running episode."""

    async def raise_interrupt(
        self, reason: InterruptReason, context: InterruptContext
    ) -> InterruptResolution:
        """Suspend, notify the shell, and wait for an operator decision.

        Blocks the deliberation tier. The control tier continues holding position, since
        a body that stops being commanded mid-motion is not safe.
        """
        raise NotImplementedError("v0.1")

    def should_raise(self, confidence: float, threshold: float) -> bool:
        """Whether this confidence warrants handing over.

        A fixed threshold is the v0.1 answer and is known to be wrong: confidence is not
        calibrated across skills, and a threshold that is right for one is noise for
        another. Per-skill calibration is v0.3 work, and until then the threshold is a
        configuration value that an operator can move.
        """
        raise NotImplementedError("v0.1")
