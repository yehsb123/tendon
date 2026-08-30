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

## The fifth time, and why the rule widened

Scanning `raise` and bare `print` missed the way this CLI actually speaks. Every command
writes through rich's `console.print`, which raises `UnicodeEncodeError` on cp949 exactly
as `print` does, and none of it was being checked. `tendon doctor` -- the first command
anyone runs, and the one whose whole job is explaining what is wrong with an install --
had thirteen lines that could not be printed on a Korean console.

Typer renders a command's docstring as its `--help` text, so those are encoded too. A
docstring is not automatically safe; it depends on who reads it.

## Why `cli/` is checked more strictly than the rest

Widening to `console.print` still missed `tendon doctor`, and the proof was not subtle:
`PYTHONIOENCODING=cp949 tendon doctor` died with `UnicodeEncodeError` while a green test
suite said the text was fine. Its findings are built as data -- `Finding(name, status,
detail, remedy)` -- and printed by a different module, so no scan of print-call arguments
can see them. Following a string from where it is written to where it is encoded is not
something a syntactic check can do.

So `cli/` gets a blunter rule: every string literal is treated as user-facing, docstrings
aside. That layer exists to produce terminal output, and a string there is destined for a
console until shown otherwise. Elsewhere the narrow rule still applies, because a message
in `services/` is as likely to be a log line or a key as something a person reads.

## What it does not cover

Docstrings outside `@app.command` functions, comments, and Korean text in commit messages
or documentation. Those are read, not encoded to a terminal, and restricting them would
cost the bilingual documentation this project deliberately keeps.
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


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Every string node that is a docstring, so `cli/`'s blanket rule can skip them.

    Prose explaining a function to a reader is not output. The exception is a command's
    docstring, which typer prints, and that is collected separately.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _is_command(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether typer will render this function's docstring as help text."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr in {"command", "callback"}:
            return True
    return False


def _user_facing_strings(path: Path) -> list[tuple[int, str]]:
    """Strings that reach a terminal: `raise`, `print`, `console.print`, and typer help.

    Walking the AST rather than grepping means a `—` inside a comment three lines above a
    `raise` is not mistaken for part of it.

    `console.print` is here because it is how every command in this CLI writes, and
    scanning only the builtin missed all of it. The match is on the attribute name rather
    than the receiver, so `console`, `err_console` and a locally built `Console()` are all
    covered without the scanner needing to know what they were called.

    Command docstrings are here because typer prints them as `--help`. A docstring is safe
    only when nothing renders it, which is a fact about the decorator, not about the string.
    """
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))

    if path.parent.name == "cli":
        docstrings = _docstring_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                found.append((node.lineno, node.value))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_command(node):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                found.append((node.lineno, doc))

        is_raise = isinstance(node, ast.Raise)
        is_print = isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == "print")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "print")
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
