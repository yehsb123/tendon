"""The uncertainty that drives the whole demonstration is a placeholder, and it says so.

`create_app` builds every session's policy with an `UncertainRegion` at joint 0, centre
0.12 rad. That region exists so the loop has something to hand over about. Its own
docstring has always called it "a stand-in for whatever makes a real policy uncertain — an
unfamiliar object, an out-of-distribution view".

**Nothing an operator could see said so.** Somebody starts an episode, watches the policy
raise its own hand at a particular joint position, and reasonably concludes the policy
knows something about itself. It does not. Everything downstream of that moment is real —
the interrupt, the safety check, the correction, the memory, the falling rate — and the
moment itself was placed there in advance.

This is the most load-bearing honesty question in the project, because the graph it
produces is the project's entire claim. The claim being made is *the loop closes*. The
claim a reader could take away is *a VLA's uncertainty behaves like this*. Only the first
is supported, and the difference has to be visible where somebody would otherwise form the
second.

## Why this is a test

The disclosure is one sentence in a view and one paragraph in a README, and both are the
kind of thing a later edit tidies away without noticing what it was for. Nothing else would
fail if they went.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")

from tendon.api.app import create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def test_the_session_reports_where_its_uncertainty_comes_from(tmp_path: Path) -> None:
    """Carried on the session rather than inferred by the shell.

    The API is what constructs the policy, so it is the only thing that knows. A shell
    that assumed the answer would keep saying it after the answer changed.
    """
    client = TestClient(
        create_app(
            skill_root=REPO / "skills",
            episode_root=tmp_path / "episodes",
            memory_root=tmp_path / "memory",
            progress_root=tmp_path / "progress",
        )
    )
    response = client.post(
        "/api/sessions",
        json={"skill": "grasp/cube-sim", "body": "mujoco", "max_steps": 5},
    )
    assert response.status_code == 200, response.text

    assert response.json()["uncertainty"] == "stand-in"


def test_the_shell_says_it_where_the_handover_is_watched() -> None:
    """In `Live`, not in a README nobody has open while a robot is moving."""
    view = (REPO / "shell/src/views/Live.tsx").read_text(encoding="utf-8")

    assert "stand-in" in view
    assert "uncertainty" in view


def test_both_readmes_say_it() -> None:
    """Beside the graph, because the graph is what a reader takes away.

    Checked in both languages: a disclosure that exists in one of them is a disclosure that
    half the readers do not get.
    """
    for name in ("README.md", "README.ko.md"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert "stand-in" in text or "대역" in text, name
        assert "0003-confidence-has-no-upstream-source" in text, name


def test_the_region_is_still_described_as_a_stand_in_in_the_code() -> None:
    """The docstring the rest of this rests on. If the region ever stops being a
    placeholder, this fails and every sentence above needs rewriting rather than quietly
    becoming wrong in the other direction."""
    source = (REPO / "src/tendon/services/adaptive.py").read_text(encoding="utf-8")

    assert "stand-in" in source


def test_the_app_still_injects_one() -> None:
    """The fact being disclosed.

    If the API ever stops constructing an `UncertainRegion` — because confidence gained a
    real upstream source — then the disclosure becomes false and has to go. Tying the two
    together here means neither can move without the other being noticed.
    """
    source = (REPO / "src/tendon/api/app.py").read_text(encoding="utf-8")

    assert "UncertainRegion(" in source
