"""Enforce the import rules stated in docs/architecture.md.

The kernel quietly pulling in a training dependency is how a layered system becomes a
monolith: nothing breaks, the diff looks harmless, and six months later the kernel cannot
run without torch. This test is the only thing that notices.

It reads source with `ast` rather than importing anything, so it runs on a bare checkout
with no extras installed — which is also what makes it meaningful in CI.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "tendon"

# Heavy backends. A layer that imports one of these has taken on its install cost.
BACKENDS = {"torch", "mujoco", "transformers", "peft", "accelerate", "lerobot", "datasets"}

# layer -> module prefixes it must not import
FORBIDDEN: dict[str, set[str]] = {
    "kernel": {"tendon.drivers", "tendon.services", "tendon.api"} | BACKENDS,
    "drivers": {"tendon.services", "tendon.api"},
    "services": {"tendon.api"},
    "api": {"tendon.drivers"},
}


def _imported_modules(path: Path) -> set[str]:
    """Top-level module names imported by a file, including dotted prefixes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _violates(imported: str, forbidden: str) -> bool:
    return imported == forbidden or imported.startswith(forbidden + ".")


def _layer_files(layer: str) -> list[Path]:
    return sorted((SRC / layer).glob("*.py"))


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_imports_stay_within_bounds(layer: str) -> None:
    forbidden = FORBIDDEN[layer]
    offences: list[str] = []

    for path in _layer_files(layer):
        for imported in _imported_modules(path):
            for rule in forbidden:
                if _violates(imported, rule):
                    offences.append(f"{path.relative_to(SRC.parent)} imports {imported}")

    assert not offences, f"{layer}/ violates docs/architecture.md:\n  " + "\n  ".join(offences)


def test_kernel_owns_the_driver_contract() -> None:
    """The Driver protocol must live in the kernel, not in drivers.

    If this fails, the kernel has started depending on whichever driver happens to be
    installed, and design decision 3 no longer holds.
    """
    protocols = SRC / "kernel" / "protocols.py"
    assert protocols.exists(), "kernel/protocols.py is missing"

    tree = ast.parse(protocols.read_text(encoding="utf-8"))
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert "Driver" in names, "Driver protocol is not defined in kernel/protocols.py"


def test_every_driver_registers_itself() -> None:
    """A driver module that forgets @register is invisible to `tendon run --driver`."""
    skip = {"__init__.py", "base.py"}
    for path in _layer_files("drivers"):
        if path.name in skip:
            continue
        source = path.read_text(encoding="utf-8")
        assert "@register(" in source, f"{path.name} defines no registered driver"
