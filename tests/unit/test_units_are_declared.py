"""Every physical quantity in the kernel vocabulary says what unit it is in.

The kernel said nothing about units. `joint_positions`, `max_joint_velocity`,
`workspace_min` and the rest were bare floats, and `kernel/safety` compares a skill's
declared limit against what a driver reports. If the two disagree about units the
comparison still succeeds and means nothing.

An arm reporting degrees makes every limit wrong by 57 — in the permissive direction — and
nothing here could have noticed, because the numbers arrive and they are numbers.

The rule was not missing. `CONTRIBUTING.md` has a section headed **"Units are mandatory on
every physical quantity"**, with `joint_positions: Vector  # [rad]` as its first example,
and the kernel it governs declared none. That is worse than an unwritten convention and
easier to miss: a rule in a document that nothing checks reads as satisfied, because the
document is still there and still says it.

Checked on the model fields rather than by reading the docstring, so the next physical
quantity added to the vocabulary fails unless it declares its unit too. A convention only
holds if breaking it is louder than following it.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from tendon.kernel import types as kernel_types

#: A unit appears in square brackets: `[rad]`, `[m/s]`, `[normalised]`. Bracketed so it can
#: be found mechanically and so a sentence mentioning metres in passing does not count.
_UNIT = "["

#: Fields that are counts, indices or scores rather than measurements. Keyed by model as
#: well as name so that exempting `Capability.dof` does not quietly exempt some future
#: `dof` that means something else. Each carries why, because "it is obvious" is how a
#: list like this stops being read.
_DIMENSIONLESS = {
    ("Capability", "dof"): "a count of axes",
    ("Observation", "step"): "an index within an episode",
    ("InterruptContext", "step"): "the same index, carried into the handover",
    ("Confidence", "score"): "a score, and ADR 0003 says it is calibrated to nothing",
    ("EpisodeMeta", "steps"): "a count of control steps",
    ("EpisodeMeta", "interrupts"): "a count of handovers",
    ("EpisodeMeta", "curation_score"): "a ranking score, not a measurement of anything",
}


def _numeric_fields() -> list[tuple[str, str, object]]:
    """Every float, int, or Vector field on every model in the kernel vocabulary."""
    found: list[tuple[str, str, object]] = []
    for name in dir(kernel_types):
        model = getattr(kernel_types, name)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        if model.__module__ != kernel_types.__name__:
            continue
        for field_name, field in model.model_fields.items():
            annotation = str(field.annotation)
            if any(kind in annotation for kind in ("float", "int")) and "bool" not in annotation:
                found.append((name, field_name, field))
    return found


@pytest.mark.parametrize(
    ("model", "field_name"),
    [(model, field_name) for model, field_name, _ in _numeric_fields()],
)
def test_a_numeric_field_says_its_unit(model: str, field_name: str) -> None:
    if (model, field_name) in _DIMENSIONLESS:
        return

    field = next(f for m, n, f in _numeric_fields() if m == model and n == field_name)
    description = field.description or ""

    assert _UNIT in description, (
        f"{model}.{field_name} is a measurement with no unit. Put it in the field's "
        f"description in brackets - [rad], [m/s], [N], [Hz], [normalised] - so it reaches "
        f"the JSON schema, or add it to _DIMENSIONLESS here with the reason."
    )


def test_the_check_is_looking_at_something() -> None:
    """A parametrized test over an empty list passes silently, which is the failure mode
    this whole file exists to prevent one layer down."""
    fields = _numeric_fields()

    assert len(fields) >= 8, f"only {len(fields)} numeric fields found; the walk is broken"
    assert ("SafetyLimits", "max_joint_velocity") in [(m, n) for m, n, _ in fields]
    assert ("Proprioception", "joint_positions") in [(m, n) for m, n, _ in fields]


def test_the_vocabulary_states_the_convention_once() -> None:
    """The per-field units say what each number is. Somebody writing a new driver needs to
    know the rule before they meet the fields, and that belongs in one place."""
    docstring = kernel_types.__doc__ or ""

    assert "SI, radians, seconds" in docstring
    assert "A driver converts" in docstring, "the rule has to say who does the converting"


def test_the_rule_this_enforces_is_the_one_the_project_states() -> None:
    """`CONTRIBUTING.md` is where the requirement is written for people. This file is
    where it is checked. If somebody softens the document, the check should stop claiming
    to enforce it rather than quietly enforcing a rule the project no longer asks for."""
    from pathlib import Path

    contributing = (Path(__file__).resolve().parents[2] / "CONTRIBUTING.md").read_text(
        encoding="utf-8"
    )

    assert "Units are mandatory on every physical quantity" in contributing


def test_the_driver_contract_repeats_it_where_a_driver_author_looks() -> None:
    """`drivers/base.py` is what a driver author imports and reads. A rule stated only in
    the kernel is a rule they meet after writing the conversion the wrong way round."""
    from tendon.drivers import base

    assert "[rad]" in (base.__doc__ or "")
