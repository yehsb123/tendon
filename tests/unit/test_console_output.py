"""Every string a user reads has to survive their console.

This has now broken four times, each time in a different place, and the last one was the
safety warning that refuses to open a physical arm. Someone pointed `open_body` at an
SO-101, tendon correctly declined, and the console died with `UnicodeEncodeError` while
printing the reason. The refusal held; the explanation did not reach anyone.

## Why a test rather than a rule

The rule was written down twice. It is in `benchmarks/README.md` under environment
findings and it was suggested for `CONTRIBUTING.md`. Prose does not stop an em dash from
being the natural character to type, and nothing in CI notices: the runners are UTF-8, so
this passes everywhere except on the machines the project is actually developed on.

A Windows console in a Korean locale runs cp949. So does a Japanese or Chinese one, with
their own code pages. None of them can encode `U+2014 EM DASH` or `U+2588 FULL BLOCK`, and
`print` raises rather than dropping the character.

## What it does not cover

Docstrings, comments, and Korean text in commit messages or documentation. Those are read,
not encoded to a terminal, and restricting them would cost the bilingual documentation this
project deliberately keeps.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "tendon"

#: Code pages a developer on this project is likely to be running. cp949 is Korean,
#: cp932 Japanese, cp936 Simplified Chinese; all three are common defaults and none is a
#: superset of ASCII plus the punctuation a writer reaches for.
CONSOLES = ("cp949", "cp932", "cp936")

NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _user_facing_strings(path: Path) -> list[tuple[int, str]]:
    """String literals inside `raise` statements and `print` calls.

    Deliberately narrow. A string that reaches a console is one that gets encoded; one that
    sits in a docstring does not. Walking the AST rather than grepping means a `—` inside a
    comment three lines above a `raise` is not mistaken for part of it.
    """
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        is_raise = isinstance(node, ast.Raise)
        is_print = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        )
        if not (is_raise or is_print):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                found.append((child.lineno, child.value))
    return found


@pytest.mark.parametrize("console", CONSOLES)
def test_error_and_print_text_encodes_on_a_non_utf8_console(console: str) -> None:
    """A message nobody can read is a message that was not sent."""
    offences: list[str] = []

    for path in sorted(SRC.rglob("*.py")):
        for line, text in _user_facing_strings(path):
            if not NON_ASCII.search(text):
                continue
            try:
                text.encode(console)
            except UnicodeEncodeError:
                # Escaped, because this assertion message is itself printed to the console
                # that cannot encode the character being reported.
                offending = "".join(sorted(set(NON_ASCII.findall(text))))
                offences.append(
                    f"{path.relative_to(SRC.parent)}:{line} contains "
                    f"{offending.encode('unicode_escape').decode()} "
                    f"({text.strip()[:40].encode('unicode_escape').decode()!r})"
                )

    assert not offences, (
        f"user-facing text that a {console} console cannot print:\n  "
        + "\n  ".join(offences)
        + "\n\nUse ASCII in messages that reach a terminal. Docstrings and comments are "
        "not restricted."
    )


def test_the_scan_finds_something_when_there_is_something_to_find() -> None:
    """The regression test's own regression test.

    A checker that silently stops checking passes forever. This gives it a file with a
    known offence and confirms it is seen, so a bug in `_user_facing_strings` shows up as
    a failure here rather than as green results and a broken console somewhere else.
    """
    source = 'def f():\n    raise ValueError("bad — thing")\n'
    scratch = Path(__file__).with_name("_console_probe.py")
    scratch.write_text(source, encoding="utf-8")
    try:
        found = _user_facing_strings(scratch)
        assert any(NON_ASCII.search(text) for _, text in found), (
            "the scanner missed an em dash inside a raise"
        )
    finally:
        scratch.unlink()
