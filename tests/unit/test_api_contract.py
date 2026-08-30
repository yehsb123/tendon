"""Keep `shell/src/api/types.ts` honest against `src/tendon/kernel/types.py`.

The TypeScript side restates the Pydantic models by hand. That duplication is a deliberate
choice — generating it would add a build step to a project that has to stay easy to run —
and this test is the price of it.

Divergence here is not a compile error on either side. It surfaces at runtime as an
operator reading a field the backend stopped sending, during an intervention, which is the
worst possible moment to discover it.

Reads both files as text rather than importing or transpiling, so it runs on a bare
checkout with no Python extras and no Node installed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / "src" / "tendon" / "kernel" / "types.py"
TS = ROOT / "shell" / "src" / "api" / "types.ts"

# Python-side names with no shell counterpart, and why. Anything not listed here is
# expected on both sides, so removing an entry is how you opt a model into the contract.
PY_ONLY: dict[str, str] = {}

# Not a model: a Pydantic config attribute that happens to look like a field.
NOT_A_FIELD = {"model_config"}


# --------------------------------------------------------------------------- python side


def _python_models() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return ({model: fields}, {enum: values}) from the Pydantic module."""
    tree = ast.parse(PY.read_text(encoding="utf-8"))
    models: dict[str, set[str]] = {}
    enums: dict[str, set[str]] = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}

        if "Enum" in bases:
            values = set()
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            values.add(str(stmt.value.value))
            enums[node.name] = values
            continue

        if "BaseModel" in bases:
            fields = set()
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    if name not in NOT_A_FIELD:
                        fields.add(name)
            models[node.name] = fields

    return models, enums


# ----------------------------------------------------------------------- typescript side


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def _typescript_models() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return ({interface: fields}, {enum: values}) from the TypeScript module."""
    source = _strip_comments(TS.read_text(encoding="utf-8"))
    interfaces: dict[str, set[str]] = {}
    enums: dict[str, set[str]] = {}

    for name, body in re.findall(r"export interface (\w+)\s*\{([^}]*)\}", source):
        fields = set(re.findall(r"^\s*(\w+)\??\s*:", body, flags=re.MULTILINE))
        interfaces[name] = fields

    for name, body in re.findall(r"export enum (\w+)\s*\{([^}]*)\}", source):
        values = set(re.findall(r'=\s*"([^"]*)"', body))
        enums[name] = values

    return interfaces, enums


# --------------------------------------------------------------------------------- tests


def test_both_files_exist() -> None:
    assert PY.exists(), f"missing {PY}"
    assert TS.exists(), f"missing {TS}"


def test_every_model_has_a_typescript_counterpart() -> None:
    py_models, _ = _python_models()
    ts_models, _ = _typescript_models()

    missing = sorted(set(py_models) - set(ts_models) - set(PY_ONLY))
    assert not missing, (
        "models defined in kernel/types.py with no interface in shell/src/api/types.ts: "
        f"{missing}. Add the interface, or record the exception in PY_ONLY with a reason."
    )


def test_no_typescript_interface_invents_a_model() -> None:
    py_models, _ = _python_models()
    ts_models, _ = _typescript_models()

    extra = sorted(set(ts_models) - set(py_models))
    assert not extra, (
        f"interfaces in types.ts with no Pydantic model behind them: {extra}. "
        "The shell cannot receive a shape the runtime does not send."
    )


@pytest.mark.parametrize("model", sorted(_python_models()[0]))
def test_fields_match(model: str) -> None:
    py_models, _ = _python_models()
    ts_models, _ = _typescript_models()

    if model in PY_ONLY or model not in ts_models:
        pytest.skip(f"{model} has no TypeScript counterpart")

    py_fields = py_models[model]
    ts_fields = ts_models[model]

    only_py = sorted(py_fields - ts_fields)
    only_ts = sorted(ts_fields - py_fields)

    assert not only_py and not only_ts, (
        f"{model} has diverged.\n"
        f"  in kernel/types.py but not types.ts: {only_py}\n"
        f"  in types.ts but not kernel/types.py: {only_ts}"
    )


@pytest.mark.parametrize("enum", sorted(_python_models()[1]))
def test_enum_values_match(enum: str) -> None:
    _, py_enums = _python_models()
    _, ts_enums = _typescript_models()

    assert enum in ts_enums, f"enum {enum} is missing from types.ts"

    only_py = sorted(py_enums[enum] - ts_enums[enum])
    only_ts = sorted(ts_enums[enum] - py_enums[enum])

    assert not only_py and not only_ts, (
        f"enum {enum} has diverged.\n"
        f"  only in Python: {only_py}\n"
        f"  only in TypeScript: {only_ts}"
    )


def test_contract_is_not_trivially_empty() -> None:
    """Guard against a parser change silently making every assertion above vacuous."""
    py_models, py_enums = _python_models()
    ts_models, ts_enums = _typescript_models()

    assert len(py_models) >= 10, f"parsed only {len(py_models)} Python models"
    assert len(ts_models) >= 10, f"parsed only {len(ts_models)} TypeScript interfaces"
    assert len(py_enums) >= 4, f"parsed only {len(py_enums)} Python enums"
    assert len(ts_enums) >= 4, f"parsed only {len(ts_enums)} TypeScript enums"
