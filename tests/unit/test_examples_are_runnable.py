"""Every example says how to run it, which is the rule its own index states.

`examples/README.md`:

    Each example is a complete scenario that runs from a clean checkout. If an example
    needs a step that is not in its README, the example is broken.

By that rule two of the four were broken. `01_record` and `04_improve` have a `run.py` and
say to run it. `02_preview` said "open the shell" and `03_intervene` said "force a
low-confidence situation", and neither said how — nine lines each, no command anywhere.

The rule was written down and nothing checked it, which is the shape that let the unit
contract sit unenforced in `CONTRIBUTING.md` for the life of the project. A rule in a
document that nothing checks reads as satisfied.

Not every example can be a script: `02` proves a person can read a plan and `03` proves a
human decision reaches the store, and no assertion settles either. What they can do is say
exactly which commands get somebody to the point where they look.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _directories() -> list[Path]:
    return sorted(path for path in EXAMPLES.iterdir() if path.is_dir() and path.name[0].isdigit())


def test_the_index_still_states_the_rule() -> None:
    """If this is ever softened, the tests below are enforcing something the project no
    longer asks for and should be reconsidered rather than left running."""
    index = (EXAMPLES / "README.md").read_text(encoding="utf-8")

    assert "runs from a clean checkout" in index
    assert "is broken" in index


def test_there_are_examples_to_check() -> None:
    """A walk that finds nothing passes every assertion below it."""
    assert len(_directories()) >= 4


@pytest.mark.parametrize("directory", _directories(), ids=lambda path: path.name)
def test_every_example_has_a_readme(directory: Path) -> None:
    assert (directory / "README.md").is_file()


@pytest.mark.parametrize("directory", _directories(), ids=lambda path: path.name)
def test_every_example_says_how_to_run_it(directory: Path) -> None:
    """A fenced block with a command in it, or a script and a line naming it.

    Checked as *a command somebody can type*, not as the word "run" appearing. Prose that
    describes the intent — "open the shell and watch" — is what both broken examples had.
    """
    readme = (directory / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```(?:bash|sh|console)?\n(.*?)```", readme, re.S)
    commands = "\n".join(blocks)

    assert commands.strip(), f"{directory.name}/README.md contains no command block"
    assert re.search(r"\b(tendon|python|npm|pip)\b", commands), (
        f"{directory.name}/README.md has a code block with nothing runnable in it"
    )


@pytest.mark.parametrize("directory", _directories(), ids=lambda path: path.name)
def test_a_script_is_named_by_the_readme_that_ships_it(directory: Path) -> None:
    """An example with a `run.py` nobody is told to run is the same defect wearing a file.

    The reverse is allowed and deliberate: `02` and `03` have no script because what they
    prove needs a person, and each says so rather than leaving the absence to be noticed.
    """
    script = directory / "run.py"
    if not script.is_file():
        return

    readme = (directory / "README.md").read_text(encoding="utf-8")
    assert "run.py" in readme, f"{directory.name} ships a script its README never mentions"


@pytest.mark.parametrize("directory", _directories(), ids=lambda path: path.name)
def test_an_example_without_a_script_says_why(directory: Path) -> None:
    """Otherwise a reader looking for the missing file concludes the repository is
    incomplete, which is the wrong conclusion about the right observation."""
    if (directory / "run.py").is_file():
        return

    readme = (directory / "README.md").read_text(encoding="utf-8").lower()
    assert "run.py" in readme, (
        f"{directory.name} has no script and does not say so; a reader will look for one"
    )
