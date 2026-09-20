"""A command shown in a code block is a command somebody will type.

`README.md` presented four commands under "a skill is a package" and one of them existed.
`src/tendon/cli/README.md` listed `install|fork|publish` in a table of nine working
commands. `skills/README.md` said `tendon install` resolves a Hub reference. None of the
three commands has been written; they are the v0.4 milestone.

Prose is different and is left alone — "tendon installs skills" in a paragraph about what
the project is *for* is a description, and the roadmap is full of them on purpose. A
fenced block is an instruction: it is formatted to be copied.

So a future command may appear in a block, and has to say so on the line, which a reader
sees and this test can check:

    tendon install  grasp/deformable-bag@1.2    # v0.4
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Directories with nothing of ours in them.
SKIP = {".git", "node_modules", "third_party", ".venv", "dist", "__pycache__"}

#: `docs/collaboration.md` is an append-only log of what was true when each line was
#: written, including commands that have since been renamed. Correcting it would be
#: falsifying a record.
EXEMPT = {"docs/collaboration.md"}

FENCED = re.compile(r"```[a-z]*\n(.*?)```", re.S)
INVOCATION = re.compile(r"^\s*(?:\$ )?tendon ([a-z][a-z|-]*)(.*)$", re.M)
FUTURE = re.compile(r"#\s*v0\.\d")


def _markdown() -> list[Path]:
    return [
        path
        for path in sorted(REPO.rglob("*.md"))
        if not SKIP & set(path.parts) and path.relative_to(REPO).as_posix() not in EXEMPT
    ]


def _real_commands() -> set[str]:
    from tendon.cli.main import app

    return {(info.name or info.callback.__name__) for info in app.registered_commands}


def _invocations() -> list[tuple[str, str, str]]:
    """(file, command, whole line) for every `tendon <word>` inside a fenced block."""
    found = []
    for path in _markdown():
        for block in FENCED.findall(path.read_text(encoding="utf-8")):
            for word, rest in INVOCATION.findall(block):
                for command in word.split("|"):
                    found.append((path.relative_to(REPO).as_posix(), command, word + rest))
    return found


def test_there_is_documentation_to_check() -> None:
    """Both assertions below walk a list. An empty one passes everything."""
    assert len(_markdown()) >= 15
    assert len(_invocations()) >= 20, "no documented invocations were found at all"
    assert len(_real_commands()) >= 10


def test_every_command_shown_in_a_code_block_exists_or_says_it_does_not() -> None:
    real = _real_commands()

    unreal = [
        f"{path}: tendon {command}"
        for path, command, line in _invocations()
        if command not in real and not FUTURE.search(line)
    ]

    assert not unreal, (
        "these are shown as commands to type and are not commands: "
        + "; ".join(sorted(set(unreal)))
        + ". Mark a planned one with a `# v0.4` comment on the line."
    )


def test_a_command_marked_future_is_one_that_really_does_not_exist_yet() -> None:
    """The other direction, so the marker cannot be left on a command after it ships and
    quietly tell readers it is unavailable."""
    real = _real_commands()

    stale = [
        f"{path}: tendon {command}"
        for path, command, line in _invocations()
        if command in real and FUTURE.search(line)
    ]

    assert not stale, "these are marked as a future version and already work: " + "; ".join(
        sorted(set(stale))
    )


@pytest.mark.parametrize("command", ["install", "fork", "publish"])
def test_the_three_that_started_this_are_still_the_planned_ones(command: str) -> None:
    """A guard on the guard. If one of these ships, the test above starts failing on its
    own `# v0.4` markers and this says which one to go and unmark."""
    assert command not in _real_commands(), (
        f"`tendon {command}` exists now - remove its `# v0.4` markers from the docs"
    )
