"""The live channel.

Messages the shell receives:

    intent      the action chunk about to execute, with confidence and target
    state       body state at the control rate, downsampled for display
    interrupt   raised, with the saved context an operator needs to decide
    resolved    the outcome of an interrupt, so every viewer stays in sync

Messages the shell sends:

    approve     let the pending intent execute
    reject      discard it and ask the policy for alternatives
    correct     replace it, subject to the same safety checks as any action
    takeover    request an operator interrupt without waiting for low confidence

Dropping this connection must never leave the body mid-motion. The control tier holds
position; the deliberation tier stops issuing new intent until the shell returns.
"""

from __future__ import annotations
