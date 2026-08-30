"""An episode that is not being kept says so, and the instructions install what keeps it.

The README's second block — the first thing somebody runs after the tests — said
`pip install -e ".[sim]"` and then, a paragraph later, that the episode is recorded. Both
could not be true. `[robot]` is what writes episodes, and without it `create_app` gets
`None` from `_open_recorder` and carries on silently.

So the documented path produced exactly the failure this project keeps finding: everything
appears to work, the handover happens, the correction is taken, the memory grows — and
`Episodes` is empty afterwards with nothing having said why.

## Why the shell had to learn to say it

`tendon run` has printed "not recording: LeRobot is not installed" since the recorder was
wired. The API returned `None` and said nothing, which makes the omission worse there than
on the command line: somebody working from the interface has fewer places to notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")

from tendon.api.app import create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def start(tmp_path: Path) -> dict:
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
    return response.json()


def test_a_session_says_whether_it_is_recording(tmp_path: Path) -> None:
    """Carried on the session because the API is what discovers it. A shell that assumed
    would keep saying it after the answer changed."""
    assert "recording" in start(tmp_path)


def test_it_reports_not_recording_when_lerobot_is_missing(tmp_path: Path, monkeypatch) -> None:
    """The documented install produced exactly this state, and nothing said so.

    Simulated by making the recorder unavailable the same way a missing extra does.
    """
    import tendon.api.app as app_module

    monkeypatch.setattr(app_module, "_open_recorder", lambda loaded, root: None)

    assert start(tmp_path)["recording"] is False


def test_the_shell_shows_it_loudly() -> None:
    """`hint-error`, not the quiet note class the other two banners use.

    Those describe how something works. This one says the work is being thrown away, and a
    dim line under the scene is the wrong weight for that.
    """
    view = (REPO / "shell/src/views/Live.tsx").read_text(encoding="utf-8")

    assert "NotRecording" in view
    assert "hint-error" in view
    assert '".[robot]"' in view


def test_the_readme_installs_what_it_promises() -> None:
    """The instruction and the sentence four lines below it have to agree.

    Checked as a property of the file rather than by remembering: the block that leads into
    "the episode is recorded" must install the extra that records.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")

    serve = readme.index("tendon serve")
    block = readme.rindex("pip install", 0, serve)
    instruction = readme[block : readme.index("\n", block)]

    assert "robot" in instruction, (
        f"the quickstart installs {instruction!r} and then says the episode is recorded; "
        "[robot] is what writes episodes"
    )
