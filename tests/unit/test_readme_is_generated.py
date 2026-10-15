"""Three parts of each README are generated, and they have to match what generates them.

The command list, the *Built on* table and the folder listing were written by hand in two
languages and drifted the way hand-kept copies do. The English listing still named a
`Training` view with no file behind it, left out `so101` and `assets/`, and its command list
had no `tendon version`; the Korean one had all three. Nobody was wrong on purpose. There
were two copies and no reason for either to notice the other.

`scripts/sync_readme.py` writes those parts between `<!-- BEGIN generated:... -->` markers.
What it cannot know, it refuses rather than guesses: a command with no Korean description,
a library in the table that `pyproject.toml` does not declare, a directory nobody has
described. An empty cell would be a README that looks finished and is not.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import typer

REPO = Path(__file__).resolve().parents[2]


def _load_sync_readme():
    """By path, because `scripts/` is not a package and should not become one for this."""
    spec = importlib.util.spec_from_file_location(
        "sync_readme", REPO / "scripts" / "sync_readme.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_readme = _load_sync_readme()


def test_the_readmes_match_what_generates_them() -> None:
    """The same check `python scripts/sync_readme.py --check` makes."""
    pending = sync_readme.pending_changes(REPO)

    assert not pending, (
        "README sections are out of date; run `python scripts/sync_readme.py`.\n"
        + "\n".join(pending.values())
    )


def test_a_command_is_read_from_the_app_with_its_arguments_and_first_help_line() -> None:
    app = typer.Typer()

    @app.command()
    def fetch(skill: str, store: str = typer.Option("")) -> None:
        """Fetch a skill.

        Everything after the first line is detail the list does not show.
        """

    @app.command("hidden-one", hidden=True)
    def hidden_one() -> None:
        """Not listed."""

    assert sync_readme.commands_of(app) == [
        sync_readme.Command("fetch", "<skill>", "Fetch a skill.")
    ]


def test_a_new_command_without_a_korean_description_stops_the_generation() -> None:
    from tendon.cli.main import app

    commands = [*sync_readme.commands_of(app), sync_readme.Command("teleport", "", "Move.")]

    with pytest.raises(sync_readme.SyncError, match="teleport") as raised:
        sync_readme.commands_block(commands, "ko")

    assert "한국어 설명" in str(raised.value)


def test_a_korean_description_for_a_command_that_is_gone_is_refused() -> None:
    """The other direction: an entry nothing reads is a sentence about a command that
    was renamed, waiting to be copied back."""
    from tendon.cli.main import app

    commands = [c for c in sync_readme.commands_of(app) if c.name != "version"]

    with pytest.raises(sync_readme.SyncError, match="version"):
        sync_readme.commands_block(commands, "ko")


def test_the_english_list_uses_the_help_text_as_written() -> None:
    commands = [sync_readme.Command("doctor", "", "Check what works here.")]

    assert sync_readme.commands_block(commands, "en") == (
        "```\ntendon doctor            Check what works here.\n```"
    )


def test_a_library_missing_from_pyproject_cannot_stay_in_the_table() -> None:
    declared = sync_readme.declared_packages((REPO / "pyproject.toml").read_text("utf-8"))
    declared.discard("torch")

    with pytest.raises(sync_readme.SyncError, match="torch"):
        sync_readme.check_built_on(sync_readme.BUILT_ON, declared)


def test_the_packages_are_read_from_both_dependency_lists() -> None:
    declared = sync_readme.declared_packages((REPO / "pyproject.toml").read_text("utf-8"))

    # One from `dependencies`, one from an extra, and the two whose requirement carries
    # its own brackets, which is what a naive `[...]` match stops early on.
    assert {"typer", "mujoco", "uvicorn", "lerobot"} <= declared
    # Comments in pyproject mention package names; they are not declarations.
    assert "gymnasium" not in declared


def test_a_planned_layer_has_to_say_so_and_has_to_still_be_planned() -> None:
    planned = sync_readme.Layer(
        packages=("huggingface-hub",),
        planned="v0.4",
        en=("Registry", "[Hub](https://example.com)", "a registry"),
        ko=("레지스트리 (v0.4 목표)", "[Hub](https://example.com)", "레지스트리"),
    )
    with pytest.raises(sync_readme.SyncError, match="v0.4"):
        sync_readme.check_built_on([planned], set())

    shipped = sync_readme.Layer(
        packages=("huggingface-hub",),
        planned="v0.4",
        en=("Registry (v0.4)", "[Hub](https://example.com)", "a registry"),
        ko=("레지스트리 (v0.4 목표)", "[Hub](https://example.com)", "레지스트리"),
    )
    with pytest.raises(sync_readme.SyncError, match="huggingface-hub"):
        sync_readme.check_built_on([shipped], {"huggingface-hub"})


def _copy_layout(tmp_path: Path) -> Path:
    """The directories the listing reads, and nothing else."""
    for relative in ("src/tendon", "shell/src", "docs/decisions", "skills", "examples", "tests"):
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    for directory in (REPO / "src/tendon").iterdir():
        if directory.is_dir() and not directory.name.startswith(("__", ".")):
            (tmp_path / "src/tendon" / directory.name).mkdir()
    for directory in (REPO / "shell/src").iterdir():
        if directory.is_dir():
            (tmp_path / "shell/src" / directory.name).mkdir()
    for path in (REPO / "src/tendon/drivers").glob("*.py"):
        shutil.copy(path, tmp_path / "src/tendon/drivers" / path.name)
    for path in (REPO / "shell/src/views").glob("*.tsx"):
        shutil.copy(path, tmp_path / "shell/src/views" / path.name)
    return tmp_path


def test_the_listing_names_every_driver_and_view_that_exists(tmp_path: Path) -> None:
    root = _copy_layout(tmp_path)
    (root / "src/tendon/drivers/franka.py").write_text("", encoding="utf-8")
    (root / "shell/src/views/Replay.tsx").write_text("", encoding="utf-8")

    for language in ("en", "ko"):
        block = sync_readme.layout_block(root, language)
        assert "franka" in block
        assert "Replay" in block


def test_a_view_that_is_gone_leaves_the_listing(tmp_path: Path) -> None:
    root = _copy_layout(tmp_path)
    (root / "shell/src/views/Curate.tsx").unlink()

    assert "Curate" not in sync_readme.layout_block(root, "en")


def test_a_directory_nobody_described_stops_the_generation(tmp_path: Path) -> None:
    root = _copy_layout(tmp_path)
    (root / "src/tendon/plugins").mkdir()

    with pytest.raises(sync_readme.SyncError, match="plugins"):
        sync_readme.layout_block(root, "ko")


def test_a_described_directory_that_is_gone_is_refused(tmp_path: Path) -> None:
    root = _copy_layout(tmp_path)
    (root / "src/tendon/assets").rmdir()

    with pytest.raises(sync_readme.SyncError, match="assets"):
        sync_readme.layout_block(root, "en")


def test_only_the_text_between_markers_is_rewritten() -> None:
    text = "before\n<!-- BEGIN generated:x -->\nold\n<!-- END generated:x -->\nafter\n"

    assert sync_readme.replace_section(text, "x", "new") == (
        "before\n<!-- BEGIN generated:x -->\n\nnew\n\n<!-- END generated:x -->\nafter\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "no markers at all\n",
        "<!-- BEGIN generated:x -->\n<!-- END generated:x -->\n<!-- BEGIN generated:x -->\n",
        "<!-- END generated:x -->\n<!-- BEGIN generated:x -->\n",
    ],
    ids=["missing", "twice", "reversed"],
)
def test_a_marker_that_is_missing_or_ambiguous_is_refused(text: str) -> None:
    """Rewriting from the wrong place would replace prose somebody wrote."""
    with pytest.raises(sync_readme.SyncError):
        sync_readme.replace_section(text, "x", "new")
