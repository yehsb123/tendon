"""Read the CLI's source as one thing, because that is what the properties are about.

Several tests assert something structural about the command layer — how many places build
a policy, whether any command runs an episode without recording it, whether a suggested
flag is one the CLI accepts. Each of them named `src/tendon/cli/main.py`.

That file was nearly two thousand lines and was split into `main`, `policies`, `observers`
and `reporting`. Every one of those tests broke, and not one of the properties had changed:
"two commands choose a policy" is true of the package whether the function lives in one
module or another. A test that names a file is a test of the file layout, which is not what
any of them meant.
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "src" / "tendon" / "cli"


def source() -> str:
    """Every CLI module, concatenated. For properties stated as text."""
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(CLI.glob("*.py")))


def trees() -> list[ast.Module]:
    """Every CLI module, parsed. For properties stated about calls or definitions."""
    return [ast.parse(path.read_text(encoding="utf-8")) for path in sorted(CLI.glob("*.py"))]


def calls_to(name: str) -> list[ast.Call]:
    """Every call to a function of this name, anywhere in the CLI.

    Matches `f(...)` and `module.f(...)` alike. Which of the two a call site uses is a
    consequence of where the function ended up living, and the properties these tests
    assert are about how many places call it, not about how they spell the path to it.

    By name rather than by argument list, for the same reason: a test that matched the call
    text broke twice — once when `--adapter` was threaded through, once when a body was —
    and neither change touched the property being asserted.
    """
    return [
        node
        for tree in trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def callers_of(name: str) -> set[str]:
    """Names of the functions that call `name`, anywhere in the CLI.

    The natural way to say "only this one place builds a policy". Comparing AST nodes for
    identity across separately parsed modules does not work — the objects differ — and the
    question was never about objects.
    """
    found: set[str] = set()
    for tree in trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and (
                    (isinstance(inner.func, ast.Name) and inner.func.id == name)
                    or (isinstance(inner.func, ast.Attribute) and inner.func.attr == name)
                ):
                    found.add(node.name)
    return found


def definitions_of(name: str) -> list[ast.FunctionDef]:
    return [
        node
        for tree in trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]


def functions_calling(method: str) -> list[ast.FunctionDef]:
    """Functions that call `something.<method>(...)`, anywhere in the CLI."""
    found: list[ast.FunctionDef] = []
    for tree in trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == method
                for inner in ast.walk(node)
            ):
                found.append(node)
    return found
