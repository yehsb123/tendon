"""`tendon serve` says whether the interface came up, or only the API.

The README promises "one command serves both the runtime and the interface". That is true
inside a checkout and only there: the mount path is `shell/dist` **relative to the working
directory**, so the same command run anywhere else brings up a working API and a blank page.

The mount was silent either way. Somebody who installed the package and ran `tendon serve`
from their home directory got exactly what the README described, minus the interface, with
nothing connecting the two — and the natural conclusion is that the project is broken rather
than that they are in the wrong directory.

The command's own help was inconsistent with the README as well. It said to run the shell
separately with `npm run dev` and never mentioned that a built shell is served, so
`tendon serve --help` and the README disagreed about what the command does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tendon.api.app import shell_root
from tendon.cli.main import app

RUNNER = CliRunner()


@pytest.fixture
def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory with no `shell/dist`, which is everywhere but a checkout."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory that looks like one, without needing a real build."""
    (tmp_path / "shell" / "dist").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_there_is_nothing_to_serve_outside_a_checkout(elsewhere: Path) -> None:
    assert shell_root() is None


def test_a_built_shell_is_found_and_named(checkout: Path) -> None:
    """Resolved rather than relative, because the point of printing it is to let somebody
    compare it against where they thought they were."""
    root = shell_root()

    assert root is not None
    assert root.is_absolute()
    assert root == (checkout / "shell" / "dist").resolve()


def test_serve_says_it_is_serving_the_api_only(elsewhere: Path, monkeypatch) -> None:
    """And says where it looked, since "no shell" is only actionable with a path.

    `uvicorn.run` is replaced: this is about what the command reports before it blocks.
    """
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    result = RUNNER.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output
    assert "API only" in result.output
    assert "npm run build" in result.output


def test_serve_names_the_shell_it_found(checkout: Path, monkeypatch) -> None:
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    result = RUNNER.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output
    assert "serving the shell" in result.output


def test_shell_does_not_send_you_to_a_second_server_for_a_page_already_served(
    checkout: Path, monkeypatch
) -> None:
    """`tendon shell` used to print "run npm run dev in another terminal" unconditionally.

    With a built interface that is advice to start a second server for a page the next line
    says is already being served. The command can see which situation it is in, so it
    should say the thing that applies.
    """
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    result = RUNNER.invoke(app, ["shell"])

    assert result.exit_code == 0, result.output
    assert "the interface is served here" in result.output
    assert "npm install" not in result.output, "told to install a shell that is already built"


def test_shell_offers_both_routes_when_there_is_no_build(elsewhere: Path, monkeypatch) -> None:
    """Build it, or run the dev server. Naming only one would leave somebody who wants the
    other to guess that it exists."""
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    result = RUNNER.invoke(app, ["shell"])

    assert result.exit_code == 0, result.output
    assert "npm run build" in result.output
    assert "npm run dev" in result.output


def test_the_shell_help_no_longer_claims_the_runtime_serves_nothing_static() -> None:
    """Its docstring explained that keeping them apart meant the runtime did not have to
    serve static files. The runtime mounts `shell/dist`, and has since one command was
    supposed to be enough."""
    result = RUNNER.invoke(app, ["shell", "--help"])

    assert "does not have to serve static files" not in result.output


def test_the_help_matches_what_the_command_does() -> None:
    """It used to describe only the dev-server workflow, which made
    `tendon serve --help` and the README disagree about whether one command is enough."""
    result = RUNNER.invoke(app, ["serve", "--help"])

    assert "shell" in result.output
