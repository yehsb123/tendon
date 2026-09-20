"""Text that reaches a terminal has to survive the terminal it reaches.

Every command in this session was run with `PYTHONIOENCODING=utf-8` in front of it, out of
habit, and nobody had checked whether it was needed. It is not — today. What the habit was
hiding is that one character in this repository cannot be printed at all on this machine:

    >>> Console().print("\\u2014")
    UnicodeEncodeError: 'cp949' codec can't encode character '\\u2014'

An em dash. There are over three hundred of them in `src/tendon`, and the reason no command
crashes is that every one sits in a docstring or comment that is never rendered — the eleven
typer commands, their option help, and every literal handed to `console.print` are ASCII.

**That is a habit, not a rule, and habits are not what a Korean or Japanese Windows console
runs on.** A default console there is cp949 or cp932, not UTF-8, and one em dash in a help
string is a traceback instead of a help screen for everybody on those machines. The failure
is also invisible to the author, whose terminal is fine.

ASCII rather than "encodable in cp949", because the narrowest console anyone will run this
on is the one to hold the line at, and because the codepage a reader has is not knowable
from here.

Prose in docstrings is untouched. Nothing renders it, and it is where the reasoning lives.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[2] / "src" / "tendon" / "cli"

#: Calls whose string arguments are written to a terminal.
PRINTERS = frozenset({"print", "echo", "secho"})


def _offenders(text: str) -> list[str]:
    return sorted({f"U+{ord(ch):04X} {ch!a}" for ch in text if ord(ch) > 127})


def _commands():
    from tendon.cli.main import app

    return [
        (info.name or info.callback.__name__, info.callback) for info in app.registered_commands
    ]


def test_there_are_commands_and_printers_to_check() -> None:
    """Both checks below walk something. An empty walk passes every assertion in it."""
    assert len(_commands()) >= 10
    assert list(CLI.rglob("*.py"))


def _command_id(value) -> str:
    return value if isinstance(value, str) else ""


@pytest.mark.parametrize("name,callback", _commands(), ids=_command_id)
def test_a_command_help_screen_is_printable_anywhere(name: str, callback) -> None:
    """`--help` renders the docstring and every option's `help=`, so both are terminal text."""
    bad = _offenders(callback.__doc__ or "")
    assert not bad, f"`tendon {name} --help` prints {bad}, which a cp949 console cannot encode"

    for parameter in inspect.signature(callback).parameters.values():
        help_text = getattr(parameter.default, "help", None)
        if isinstance(help_text, str):
            bad = _offenders(help_text)
            assert not bad, f"`tendon {name} --{parameter.name}` help prints {bad}"


def test_every_literal_the_cli_prints_is_printable_anywhere() -> None:
    """The other half. A help screen is not the only thing a person reads."""
    offenders = []
    for path in sorted(CLI.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in PRINTERS:
                continue
            for arg in ast.walk(node):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    bad = _offenders(arg.value)
                    if bad:
                        offenders.append(f"{path.name}:{node.lineno} {bad}")

    assert not offenders, "printed and unprintable on a cp949 console: " + "; ".join(offenders)


def test_the_check_catches_the_character_it_was_written_for(tmp_path: Path) -> None:
    """Both assertions above pass today, so either could be a walk that finds nothing.

    An em dash is the one that actually occurs here, so it is the one planted.
    """
    em_dash = "—"

    with pytest.raises(UnicodeEncodeError):
        em_dash.encode("cp949")

    assert _offenders(f"run a skill {em_dash} once") == ["U+2014 '\\u2014'"]
    assert _offenders("run a skill - once") == [], "plain ASCII must not be reported"

    planted = tmp_path / "printer.py"
    planted.write_text(f'console.print("done {em_dash} finally")\n', encoding="utf-8")
    found = [
        arg.value
        for node in ast.walk(ast.parse(planted.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in PRINTERS
        for arg in ast.walk(node)
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _offenders(arg.value)
    ]
    assert found, "the printed-literal walk would not have seen a planted em dash"
