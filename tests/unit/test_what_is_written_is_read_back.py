"""Anything this project writes to disk, it has to be able to read back.

`services/progress.py` wrote `EpisodeRecord.succeeded` on every append and `_read` never
named it, so every verdict ever logged came back `None` — and `tendon eval` reported 0.0%
success for the same episodes `tendon progress` called unmeasured. The instance is fixed.
This is the class.

Three modules write their own JSON and read it back: `progress`, `calibration`,
`memory_store`. Each pairs a writer with a reader that were written separately, and in each
the writer is the easy half — `record.__dict__` or `asdict(...)` carries a new field for
free, while the reader has to be edited by somebody who remembers. **The default outcome of
adding a field is that it is dropped on arrival.**

The registry below is small and would go stale, which this repository has learned the hard
way more than once — so the first test checks it against the package rather than trusting
it. A fourth module that starts round-tripping JSON fails that test until it is listed.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[2] / "src" / "tendon" / "services"

#: (module, dataclass) pairs whose instances are written as JSON and read back by the same
#: module. The class need not be defined there — `CorrectionMemory` lives in `adaptive`.
PERSISTED = (
    ("tendon.services.progress", "EpisodeRecord"),
    ("tendon.services.calibration", "Calibration"),
    ("tendon.services.memory_store", "CorrectionMemory"),
)


def _round_tripping_modules() -> set[str]:
    """Modules that both write and read JSON, which is what makes a reader able to lose a
    field the writer emitted. A module that only reads somebody else's format — `store`
    reading LeRobot metadata, `policy_lerobot` reading a PEFT config — cannot."""
    found = set()
    for path in sorted(SERVICES.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "json.dumps" in source and "json.loads" in source:
            found.add(f"tendon.services.{path.stem}")
    return found


def _keys_read(source: str) -> set[str]:
    """Every string key pulled out of a mapping: `raw["x"]`, `raw.get("x")`, `entry["x"]`."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
    return keys


def test_the_registry_names_every_module_that_round_trips_json() -> None:
    """A list you have to remember to update is a list that will be wrong — `bodies.py`
    says so about drivers and it is just as true here."""
    listed = {module for module, _ in PERSISTED}

    assert listed == _round_tripping_modules(), (
        "these write and read their own JSON and are not covered: "
        f"{sorted(_round_tripping_modules() - listed)}"
    )


@pytest.mark.parametrize("module_name,class_name", PERSISTED, ids=[c for _, c in PERSISTED])
def test_every_persisted_field_is_read_back(module_name: str, class_name: str) -> None:
    """Each declared field must appear as a key the module reads out of the parsed JSON.

    Reading the key is necessary and not sufficient — a reader could read it and drop it on
    the floor — which is why `test_the_verdict_survives_the_disk.py` also round-trips real
    values through `append` and `history`. This one catches the field nobody thought about
    at all, which is the one that actually happened.
    """
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name, None)
    if cls is None:  # imported under its own name from elsewhere
        cls = getattr(importlib.import_module("tendon.services.adaptive"), class_name)

    declared = {field.name for field in dataclasses.fields(cls)}
    assert declared, f"{class_name} declares no fields, so this test checks nothing"

    read = _keys_read(Path(module.__file__).read_text(encoding="utf-8"))
    missing = sorted(declared - read)

    assert not missing, (
        f"{module_name} writes {class_name}.{missing} and never reads those keys back; "
        f"they return as their defaults, silently"
    )


def test_the_check_would_notice_a_field_nobody_reads() -> None:
    """Every assertion above passes today, so each could be comparing empty sets."""
    reader = 'raw["skill"]\nraw.get("steps")\n'

    assert _keys_read(reader) == {"skill", "steps"}
    assert {"skill", "steps", "succeeded"} - _keys_read(reader) == {"succeeded"}
    assert _round_tripping_modules(), "no module was found to round-trip JSON at all"
