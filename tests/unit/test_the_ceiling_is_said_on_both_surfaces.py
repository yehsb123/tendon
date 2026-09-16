"""A machine ceiling that narrowed a skill's limits is disclosed wherever a run starts.

`~/.tendon/limits.yaml` can be tighter than what a skill asks for, and when it is, the
tighter bound is what the scheduler enforces. `tendon run` says so on every run — *"local
limits are tighter than the skill's; using the tighter"*.

The shell said it on the `Skills` page, which somebody visits to read about a skill, and
not on `Live`, which is where they press start. **The same fact disclosed on one surface
and withheld on the other, and the withheld one is the operator's.**

Checked structurally rather than by driving a browser. What can be asserted here is that
neither surface loses the disclosure: the CLI still prints it, the shell still declares the
flag, and the view that starts a run still reads it. A screenshot test would check the
pixels and miss the thing that actually went wrong, which was that one of the two places
never asked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHELL = REPO / "shell" / "src"


def test_the_cli_says_it_on_every_run() -> None:
    """`_effective_limits` is the one place a scheduler's bounds are decided, and it is
    called by `run`, `eval` and `calibrate`. The notice belongs with the decision."""
    import inspect

    from tendon.cli.main import _effective_limits

    source = inspect.getsource(_effective_limits)

    assert "tighter" in source
    assert "console.print" in source, "the decision is made and nobody is told"


def test_the_runtime_sends_the_flag() -> None:
    """`capped` is computed once, in the detail route, from the skill's own numbers against
    the effective ones. A shell that had to compare them itself would be a second copy of
    that comparison."""
    source = (REPO / "src" / "tendon" / "api" / "app.py").read_text(encoding="utf-8")

    assert '"capped"' in source
    assert '"declared"' in source, "without both numbers a reader cannot see what changed"


def test_the_shell_declares_it() -> None:
    source = (SHELL / "api" / "client.ts").read_text(encoding="utf-8")

    assert "capped: boolean;" in source
    assert "declared:" in source


@pytest.mark.parametrize("view", ["Skills.tsx", "Live.tsx"])
def test_both_surfaces_read_it(view: str) -> None:
    """The page that describes a skill and the page that starts one.

    `Live` was the one missing it, which is the wrong way round: somebody reading `Skills`
    is deciding whether to care, and somebody on `Live` is about to move a robot under
    bounds they did not choose.

    Asserted on the *use*, not on the word appearing. The first version of this checked
    `"capped" in source`, and deleting the component from `Live` left it passing — the
    docstring explaining why the component existed still contained the word. A test that a
    comment mentions something is not a test that anything reads it.
    """
    source = (SHELL / "views" / view).read_text(encoding="utf-8")
    without_comments = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    without_comments = re.sub(r"//.*", "", without_comments)

    assert ".capped" in without_comments, (
        f"{view} never reads the flag outside a comment, so the ceiling is not disclosed "
        f"where somebody can see it"
    )


def test_live_fetches_the_detail_it_needs() -> None:
    """The flag only reaches `Live` because `choose` asks for it. A view reading a field
    nothing populates would show nothing and look correct."""
    source = (SHELL / "state" / "session.ts").read_text(encoding="utf-8")

    assert "chosenDetail" in source
    assert "api.skill(" in source, "nothing fetches the detail the flag lives on"


def test_a_stale_cap_notice_cannot_survive_a_new_choice() -> None:
    """A notice left over from another skill is worse than none: it is true of a motion
    that is not the one about to run."""
    source = (SHELL / "state" / "session.ts").read_text(encoding="utf-8")
    choose = source.partition("async choose(")[2].partition("\n  async ")[0]

    assert "chosenDetail: null" in choose, "the previous skill's detail is carried forward"
