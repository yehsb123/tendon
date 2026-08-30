"""`SECURITY.md` says what the system will not do. It has to be right.

This document had drifted in the worst possible direction. Two paragraphs apart it said a
physical driver exists and that the `so101` driver "is v0.4 work — until it exists, and
until the scheduler actually routes every action through `kernel/safety`, connecting this
to a robot means running a policy with no limit enforcement at all". Both halves were
written truthfully and the second had gone stale: the driver landed, and the scheduler
grew a single `driver.apply` call site with a safety check in front of it.

A reader deciding whether to connect an arm got two contradictory answers about whether
limits are enforced, and the alarming one was the false one.

It also claimed that losing the shell "stops new intent at the deliberation tier". It does
not. `api/app.py` returns from the socket handler and says why — a viewer going away is not
a reason to stop a moving body — so an episode continues unattended to its step limit. That
is a defensible design and it is not what the document said.

## What can be checked here, and what cannot

Not "is the system safe". These assert that the specific, mechanical claims the document
makes are still true of the code: a call site, a class, a driver file. The judgement calls —
whether the limits are the right limits — are exactly what the document says have never been
verified against a real body, and no test can say otherwise.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SECURITY = (REPO / "SECURITY.md").read_text(encoding="utf-8")


def test_the_physical_driver_it_names_exists() -> None:
    """The document opens by saying `drivers/so101.py` exists, which is the sentence that
    makes the rest of the notice necessary."""
    assert "drivers/so101.py" in SECURITY
    assert (REPO / "src/tendon/drivers/so101.py").is_file()


def test_there_is_exactly_one_place_an_action_reaches_a_body() -> None:
    """The invariant the whole document rests on.

    Counted with the parser rather than by grepping, so a `driver.apply` inside a comment
    or a docstring cannot make this pass or fail for the wrong reason.
    """
    source = (REPO / "src/tendon/kernel/scheduler.py").read_text(encoding="utf-8")

    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "apply"
    ]

    assert len(calls) == 1, (
        f"{len(calls)} call sites reach a driver. SECURITY.md's first listed safety issue "
        "is an action reaching a driver without passing kernel/safety, and one call site is "
        "what makes that checkable by reading."
    )


def test_a_correction_that_exceeds_a_limit_has_somewhere_to_go() -> None:
    """ "An operator can correct but not exceed." The interface must not be a way around a
    bound, and the scheduler raises rather than clamping silently — a human told their
    correction was applied when it was altered has been told something false."""
    from tendon.kernel.scheduler import UnsafeCorrection

    assert issubclass(UnsafeCorrection, Exception)
    assert "correct but not exceed" in SECURITY


def test_an_interrupt_that_cannot_resume_is_reported_as_a_fault() -> None:
    """Reporting a degraded interrupt as a normal one makes the intervention rate look
    better than it is, and that number is the one thing this project is judged on."""
    from tendon.kernel.scheduler import EpisodeResult

    assert "fault_reason" in EpisodeResult.__dataclass_fields__
    assert "is not a stop" in SECURITY


def test_the_document_does_not_claim_a_disconnect_stops_the_policy() -> None:
    """It did, and it was not true.

    It did, while the code did nothing of the kind. The behaviour exists now — an episode
    that loses its last operator stops proposing new motion, and a pending decision is
    given up on rather than waited out — so the document may say so.

    What is pinned is the *shape* of the old sentence, which described the property in the
    abstract with nothing behind it. The replacement has to name what actually stops it, so
    a reader can go and check.
    """
    assert "stops new intent at the deliberation tier" not in SECURITY
    assert "declines to ask for another" in SECURITY
    assert "aborted, never approved" in SECURITY


def test_the_unimplemented_gaps_are_still_named() -> None:
    """The document's value is the list of things it says are missing. A revision that
    quietly dropped one would read as progress."""
    for gap in ("authentication", "has been verified against a real"):
        assert gap in SECURITY, gap


def test_the_ceiling_over_a_skill_is_described_and_real() -> None:
    """The gap this document tracked as required work before v0.4, now closed.

    Both halves checked together: the document says a machine can cap what a skill asks
    for, and the module that does it exists. A notice describing a control that is not
    there is the failure mode this file was written for.
    """
    from tendon.kernel.types import SafetyLimits
    from tendon.services.limits import tighten

    assert "limits.yaml" in SECURITY
    assert "stricter of the two" in SECURITY

    capped = tighten(SafetyLimits(max_joint_velocity=99.0), SafetyLimits(max_joint_velocity=2.0))
    assert capped.max_joint_velocity == 2.0
