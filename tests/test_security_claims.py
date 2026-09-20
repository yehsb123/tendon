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


def test_the_ceiling_cannot_be_used_to_widen_a_skills_own_bound() -> None:
    """The other direction, and the one that makes it a control rather than a setting.

    A local file that could loosen a skill's limit would be a way to disable a safety bound
    by editing a config, which is what the document says it cannot be. Worth asserting
    separately: `tighten` passing the narrowing case tells you nothing about the widening
    one, and a reader is being told to rely on this.
    """
    from tendon.kernel.types import SafetyLimits
    from tendon.services.limits import tighten

    assert "loosen" in SECURITY, "the document has to state the direction, not imply it"

    widened = tighten(SafetyLimits(max_joint_velocity=1.0), SafetyLimits(max_joint_velocity=500.0))
    assert widened.max_joint_velocity == 1.0, "the skill's own bound has to survive"


def test_the_document_does_not_blame_a_command_that_does_not_exist() -> None:
    """ "Skills are remote code" used to be attributed to `tendon install`, which is v0.4.

    A safety notice pointing at a future command reads as a future risk. It is live now:
    `tendon run <path>` loads any `skill.yaml`, that file declares the safety limits the
    arm runs under, and no local ceiling is configured by default. Measured — a copy of
    this repository's own skill with `max_joint_velocity: 99.0` loads and 99.0 stands.
    """
    from tendon.services.limits import load_local_limits

    remote = SECURITY.split("**Skills are remote code")[1].split("\n\n**")[0]

    assert "v0.4" in remote, "say that install does not exist yet"
    assert "tendon run" in remote, "and name the route that does load a skill today"

    assert load_local_limits(REPO / "no-such-limits.yaml") is None, (
        "an absent ceiling is the default, which is why the risk is live"
    )


def test_a_skill_from_any_path_supplies_the_bounds_it_runs_under() -> None:
    """The mechanism behind that paragraph, checked rather than described.

    Nothing about `load_skill` requires a skill to come from this repository, and the
    limits it returns are the ones the file asked for.
    """
    import tempfile

    import yaml

    from tendon.services.skill import load_skill

    raw = yaml.safe_load((REPO / "skills" / "grasp" / "cube-sim" / "skill.yaml").read_text("utf-8"))
    raw["safety"]["max_joint_velocity"] = 99.0

    directory = Path(tempfile.mkdtemp()) / "handed-to-you"
    directory.mkdir()
    (directory / "skill.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert load_skill(directory).limits.max_joint_velocity == 99.0, (
        "a file from anywhere proposes the bound; that is what the document has to say"
    )
