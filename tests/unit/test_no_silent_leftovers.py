"""Defects a rename leaves behind, which neither ruff nor mypy has anything to say about.

A bulk rename during the `cli/main.py` split turned the *definition* of
`_RUNNABLE_POLICIES` into `policies.RUNNABLE_POLICIES = frozenset(...)` — a statement in
one module reaching into another and rebinding its attribute at import. It assigned the
same value, so nothing broke, nothing complained, and it sat there through a full suite,
a lint pass and a type check. Two dead constants sat beside it.

**A rename that edits a definition as though it were a call site leaves working code that
means something else.** The tools are silent because both are legal Python: rebinding a
module attribute is a technique, and an unused module-level constant is not an error.

Each check below is a function that takes source and returns what it found, so the tree
and the planted sample go through the same implementation. A check whose proof is a
separate copy of its logic can rot while the proof stays green — which is the failure this
file exists to prevent, and it would be a poor showing to reproduce it here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "src" / "tendon"
TESTS = REPO / "tests"

PLANTED = (
    "import os\n"
    "os.SOMETHING = 1\n"
    "UNUSED_CONSTANT = 2\n"
    "\n"
    "def check():\n"
    "    assert 1 == 1 or True\n"
    "    assert 'nonempty'\n"
)


def _modules(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def rebindings(source: str) -> list[int]:
    """Lines where a module rebinds another module's attribute at import time.

    Top-level only. Inside a function it is usually a fixture or a deliberate patch, and
    `monkeypatch` exists for exactly that; at import time it is a module quietly editing
    one it merely imported.
    """
    return [
        node.lineno
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Attribute) for target in node.targets)
    ]


def constant_names(source: str) -> list[tuple[str, int]]:
    """Module-level constants and private module state, with the line they are bound on.

    Public lowercase names are skipped: at module level one is usually a singleton that
    something imports. Dunders are skipped too — `__all__` holds strings, not references.
    """
    found = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id.startswith("__"):
            continue
        if target.id.isupper() or target.id.startswith("_"):
            found.append((target.id, node.lineno))
    return found


def vacuous_assertions(source: str) -> list[tuple[int, str]]:
    """Assertions that hold whatever the code under test does.

    `assert ... or True` was written in this repository while replacing a test that had
    broken during a refactor. Nothing else catches either shape: pytest runs them, they
    pass, and the suite comes out greener than the code.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if isinstance(test, ast.Constant) and test.value:
            found.append((node.lineno, "assert <truthy constant>"))
        elif (
            isinstance(test, ast.BoolOp)
            and isinstance(test.op, ast.Or)
            and any(isinstance(v, ast.Constant) and v.value for v in test.values)
        ):
            found.append((node.lineno, "assert ... or True"))
    return found


def test_every_check_sees_a_planted_offender() -> None:
    """Each assertion below passes today, so each could be a walk that finds nothing.

    This runs the same three functions over a sample carrying one of each. It is the only
    reason to believe a green result here means the tree is clean rather than unread.
    """
    assert rebindings(PLANTED) == [2]
    assert ("UNUSED_CONSTANT", 3) in constant_names(PLANTED)
    assert [reason for _, reason in vacuous_assertions(PLANTED)] == [
        "assert ... or True",
        "assert <truthy constant>",
    ]


def test_there_are_modules_to_check() -> None:
    """A walk over an empty list passes everything below it."""
    assert len(_modules(PACKAGE)) >= 20
    assert len(_modules(TESTS)) >= 20


@pytest.mark.parametrize("root", [PACKAGE, TESTS], ids=["src", "tests"])
def test_no_module_rebinds_another_module_s_attribute_at_import(root: Path) -> None:
    offenders = [
        f"{path.relative_to(REPO)}:{line}"
        for path in _modules(root)
        for line in rebindings(path.read_text(encoding="utf-8"))
    ]

    assert not offenders, "these rebind another module's attribute at import: " + "; ".join(
        offenders
    )


def test_no_module_level_constant_is_defined_and_never_read() -> None:
    """Dead constants are where a moved function leaves its shadow.

    Counted across the package by name, which is coarse — a name occurring once in the
    whole package is dead, a name occurring more is assumed live. Coarse in the safe
    direction: it can miss a leftover, and it cannot invent one.
    """
    sources = {path: path.read_text(encoding="utf-8") for path in _modules(PACKAGE)}

    dead = [
        f"{path.relative_to(REPO)}:{line} {name}"
        for path, text in sources.items()
        for name, line in constant_names(text)
        if sum(other.count(name) for other in sources.values()) <= 1
    ]

    assert not dead, "defined and read nowhere: " + "; ".join(dead)


def test_no_assertion_is_true_whatever_the_code_does() -> None:
    vacuous = [
        f"{path.relative_to(REPO)}:{line} {reason}"
        for path in _modules(TESTS)
        for line, reason in vacuous_assertions(path.read_text(encoding="utf-8"))
    ]

    assert not vacuous, "these pass whatever the code does: " + "; ".join(vacuous)
