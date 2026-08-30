"""`pytest tests/unit` has to work on `pip install -e ".[dev]"`.

That pair is the README's first instruction, under the heading *"Nothing here needs a GPU,
a robot, or a simulator"* — the first thing anybody runs, and the sentence that decides
whether they keep going.

Nothing checked it. The CI unit job installs `[dev,view]`, and deliberately: without
rerun-sdk the whole of `test_viz.py` skips, and that is 27 tests covering a bus subscriber
that must not be able to take a run with it. Good reason, and it leaves the documented path
unverified — the job that looks like it tests the claim is testing something else.

Verified by hand in a clean virtualenv: 443 passed, 15 skipped. So the claim is true today.
This is what keeps it true.

## Why static rather than a second CI job

A job would be honest and slow, and it would find the problem at the same time as this
does. The failure mode is narrow enough to name: a unit test importing an optional package
at module level, which turns a skip into a collection error and takes the whole run with it
— one file, and `pytest tests/unit` reports nothing at all.

## An aside worth keeping

Two attempts to check this by faking an uninstalled environment both produced false alarms.
Wrapping `builtins.__import__` let `importorskip` past it and then broke rerun's own
internal imports halfway. A meta-path finder that *raised* made `importlib.util.find_spec`
raise, where a genuinely absent module returns None — so a correct guard in
`test_policy_adapter.py` looked broken. Neither was a defect in this repository, and both
looked exactly like one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UNIT = REPO / "tests" / "unit"


def optional_packages() -> set[str]:
    """Import names supplied only by an extra, read from `pyproject.toml`.

    Derived rather than listed, so adding an extra does not silently leave a hole here.
    Distribution names are mapped to import names where they differ: `rerun-sdk` installs
    `rerun`, and `uvicorn[standard]` is a hard dependency in any case.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    start = text.index("[project.optional-dependencies]")
    end = text.find("\n[", start + 1)
    section = text[start : end if end != -1 else len(text)]

    # Split into `name = [...]` blocks. `dev` spans several lines with comments between
    # them, so skipping by line leaves its contents behind — `pytest` came through that way
    # and flagged every test file in the directory.
    blocks = re.findall(r"^(\w+)\s*=\s*\[(.*?)\]", section, re.MULTILINE | re.DOTALL)

    names = set()
    for extra, body in blocks:
        # `dev` is what the README tells you to install, so nothing it supplies is optional
        # for this question. `all` only names other extras.
        if extra in {"dev", "all"}:
            continue
        for requirement in re.findall(r'"([A-Za-z][A-Za-z0-9_.\-]*)', body):
            names.add(requirement.replace("-", "_").lower())

    # `rerun-sdk` is the distribution; `rerun` is what you import.
    if "rerun_sdk" in names:
        names.add("rerun")
    return names


def module_level_imports(path: Path) -> set[str]:
    """Root package names imported at module level, before any guard could run."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_no_unit_test_imports_an_extra_at_module_level() -> None:
    """The failure this protects against is not one test failing.

    A module-level import of a missing package is a **collection error**, and one of those
    stops the whole run: `pytest tests/unit` prints an error and no results at all. Somebody
    following the README would conclude the project does not work, from a file they were
    never going to care about.

    Imports inside a function or after `pytest.importorskip` are fine, which is why this
    reads the module body rather than the whole tree.
    """
    optional = optional_packages()

    offenders = []
    for path in sorted(UNIT.glob("*.py")):
        for name in sorted(module_level_imports(path) & optional):
            offenders.append(f"{path.relative_to(REPO)} imports {name}")

    assert not offenders, (
        "these turn a missing extra into a collection error that takes the whole unit run "
        f"with it: {offenders}. Move the import inside the test, or guard the module with "
        "pytest.importorskip."
    )


def test_the_scan_knows_what_the_extras_are() -> None:
    """A set that came back empty would make the check above pass on nothing."""
    optional = optional_packages()

    assert {"mujoco", "lerobot", "torch", "rerun"} <= optional, optional


def test_it_reads_the_unit_directory() -> None:
    """And a glob that matched nothing would do the same."""
    assert len(list(UNIT.glob("*.py"))) > 10


def test_a_guarded_module_is_not_reported() -> None:
    """`test_viz.py` calls `importorskip` and then imports the module under test. That is
    the correct shape and must not be flagged — a check that punished it would push people
    towards deleting the guard rather than keeping it."""
    viz = UNIT / "test_viz.py"

    assert "importorskip" in viz.read_text(encoding="utf-8")
    assert not (module_level_imports(viz) & optional_packages())
