"""Every store that defaults into a home directory is redirected during tests.

`tests/conftest.py` was written to stop the suite writing into the person running it. One
round later a third store was added and the guard was not updated, so the next run put real
files under a real `~/.tendon/progress` — the exact accident the guard exists to prevent,
repeated by the person who had just prevented it. The one after that turned out never to
have covered `recorder.DEFAULT_ROOT` either.

Two lessons, and this file is the second one. A list of things to remember is a list that
will be wrong; the fix is to check it against reality rather than to try harder.

So: find every module-level constant in `src/tendon/services/` that resolves under
`Path.home()`, and require the conftest to name it. Adding a fourth store fails here, in a
file whose whole purpose is to say what to do about it, rather than silently in somebody's
home directory a week later.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[1] / "src" / "tendon" / "services"


def guarded_roots() -> tuple[tuple[str, str, str], ...]:
    """`GUARDED_ROOTS` from the conftest beside this file.

    Loaded by path rather than imported as `tests.conftest`: an unrelated `tests` package
    in site-packages shadows the name, and a test that silently checked somebody else's
    module would be worse than no test.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "conftest.py"
    spec = importlib.util.spec_from_file_location("tendon_test_conftest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GUARDED_ROOTS


GUARDED_ROOTS = guarded_roots()


def declared_home_roots() -> set[tuple[str, str]]:
    """Module-level names in `services/` assigned something built from `Path.home()`.

    Read from the source rather than by importing, so a module needing an optional extra
    is still checked. The pattern is deliberately narrow — an assignment whose right-hand
    side mentions `Path.home()` — because that is exactly the shape being guarded against,
    and a looser match would start reporting things nobody can act on.
    """
    found: set[tuple[str, str]] = set()

    for path in sorted(SERVICES.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        module = f"tendon.services.{path.stem}"

        for node in ast.parse(source).body:
            if not isinstance(node, ast.Assign):
                continue
            text = ast.get_source_segment(source, node.value) or ""
            if "Path.home()" not in text:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.add((module, target.id))

    return found


def test_the_guard_covers_every_store() -> None:
    guarded = {(module, attribute) for module, attribute, _ in GUARDED_ROOTS}
    declared = declared_home_roots()

    missing = sorted(declared - guarded)
    assert not missing, (
        f"{missing} default into a home directory and tests/conftest.py does not redirect "
        "them. Add each to GUARDED_ROOTS with the subdirectory it should use, or the next "
        "test run writes into whoever is running it."
    )


def test_the_guard_does_not_name_things_that_do_not_exist() -> None:
    """The other direction. A guard entry for a constant that has been renamed or removed
    is a line that reads as protection and is not."""
    declared = declared_home_roots()
    stale = sorted({(m, a) for m, a, _ in GUARDED_ROOTS} - declared)

    assert not stale, f"tests/conftest.py guards {stale}, which no longer exist"


def test_the_scan_finds_something() -> None:
    """A pattern that matched nothing would make both tests above pass on empty sets."""
    assert len(declared_home_roots()) >= 3


def test_nothing_under_the_real_home_is_touched(tmp_path: Path) -> None:
    """The property itself, checked once at runtime rather than only structurally.

    Reads the modules through the running fixtures, so this fails if the redirect stops
    being applied for any reason the source scan cannot see.
    """
    import importlib

    for module_name, attribute, _ in GUARDED_ROOTS:
        module = importlib.import_module(module_name)
        value = Path(getattr(module, attribute))

        assert (
            not re.match(r"^.*[\\/]\.tendon[\\/]", str(value)) or Path.home() not in value.parents
        ), f"{module_name}.{attribute} is {value}, which is under the real home directory"
