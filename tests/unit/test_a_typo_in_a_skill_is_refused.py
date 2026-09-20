"""One letter in `skill.yaml`, and the system does something other than what the file says.

The `safety` block already refused unknown keys, with the reason written on it: "a
misspelled limit is not enforced, and nothing downstream would report it as missing." The
same sentence is true of the other three blocks and the check was never extended to them.
Measured before the fix, one letter each:

    interrupt.confidence_treshold   loaded, threshold silently 0.5
    policy.bases                    loaded, no base policy at all
    eval.successs                   loaded, no success criteria
    safety.max_joint_velocty        refused

`eval.successs` is the worst of them. No criteria means `judge_result` returns `None` for
every episode, the report says success could not be measured, and **that is exactly the
state this project has been in for weeks for an unrelated reason** — so it is the last
result anyone would question. A skill author could land there by typo and read their own
report as confirmation that the rig cannot judge.

The top level stays open. That is where forward compatibility actually lives: a later
tendon adds a `training:` block far more readily than it adds a key inside `interrupt:`,
and adding one there is a format change under an `apiVersion` that still says `v1alpha1`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from tendon.services.skill import _CLOSED_BLOCKS, SkillError, load_skill

REPO = Path(__file__).resolve().parents[2]
REAL = REPO / "skills" / "grasp" / "cube-sim" / "skill.yaml"
LOADER = REPO / "src" / "tendon" / "services" / "skill.py"


def _written(tmp_path: Path, raw: dict) -> Path:
    directory = tmp_path / "skill"
    directory.mkdir(exist_ok=True)
    (directory / "skill.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return directory


def _real() -> dict:
    return yaml.safe_load(REAL.read_text(encoding="utf-8"))


def test_the_skill_this_repository_ships_still_loads(tmp_path: Path) -> None:
    """Every refusal below is worthless if the check also refuses the real file."""
    assert load_skill(_written(tmp_path, _real())).ref == "grasp/cube-sim"


@pytest.mark.parametrize(
    ("block", "correct", "typo"),
    [
        ("safety", "max_joint_velocity", "max_joint_velocty"),
        ("interrupt", "confidence_threshold", "confidence_treshold"),
        ("policy", "base", "bases"),
        ("eval", "success", "successs"),
    ],
)
def test_a_one_letter_typo_is_refused_by_name(
    tmp_path: Path, block: str, correct: str, typo: str
) -> None:
    raw = _real()
    raw[block][typo] = raw[block].pop(correct)

    with pytest.raises(SkillError) as caught:
        load_skill(_written(tmp_path, raw))

    message = str(caught.value)
    assert typo in message, "the refusal has to name the key, or it is a puzzle"
    assert correct in message, "and the one it was probably meant to be"
    assert block in message


def test_a_block_a_later_tendon_adds_still_loads(tmp_path: Path) -> None:
    """The cost of strictness, paid where it is cheapest. A whole unknown block is far
    likelier to be a feature than a typo; an unknown key inside a four-key block is not."""
    raw = _real()
    raw["training"] = {"epochs": 3, "lr": 1e-4}

    assert load_skill(_written(tmp_path, raw)).ref == "grasp/cube-sim"


def test_an_absent_block_is_not_an_empty_one(tmp_path: Path) -> None:
    """Dropping a block entirely is a legitimate skill, not a near-miss."""
    raw = _real()
    del raw["interrupt"]

    assert load_skill(_written(tmp_path, raw)).confidence_threshold == 0.5


@pytest.mark.parametrize("block", sorted(_CLOSED_BLOCKS))
def test_every_key_declared_known_is_a_key_the_loader_reads(block: str) -> None:
    """The closed set and the parser are two copies of one fact.

    A key listed as known but read by nothing is the original bug holding a permit: the
    file says it, the loader accepts it, and the system ignores it. Checked against the
    string literals the loader actually looks up rather than against a second list.
    """
    tree = ast.parse(LOADER.read_text(encoding="utf-8"))

    looked_up: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            looked_up.add(node.args[0].value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            looked_up.add(node.slice.value)

    accepted_but_ignored = sorted(_CLOSED_BLOCKS[block] - looked_up)

    assert not accepted_but_ignored, (
        f"the {block} block accepts {accepted_but_ignored} and the loader never reads "
        f"them, so a skill can set them and nothing happens"
    )


def test_the_closed_blocks_are_the_ones_the_loader_actually_parses() -> None:
    """A block parsed with no closed set is a block where a typo is still silent.

    Read off `load_skill`'s own `_mapping(raw, "<name>", ...)` calls, so a fifth block
    added tomorrow fails this until somebody decides whether its keys are closed.
    """
    tree = ast.parse(LOADER.read_text(encoding="utf-8"))

    parsed: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_mapping"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            parsed.add(node.args[1].value)

    # `metadata` is required and checked field by field a few lines below, which is its
    # own near-miss check: a misspelled `name` there is a load error already.
    parsed.discard("metadata")

    assert parsed == set(_CLOSED_BLOCKS), (
        f"{sorted(parsed - set(_CLOSED_BLOCKS))} are parsed as blocks with no closed key "
        f"set, so a typo inside them is still silent"
    )
