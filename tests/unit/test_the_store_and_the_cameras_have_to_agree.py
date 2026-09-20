"""The tool suggested the flag that breaks against the store the tool had just filled.

`tendon run` prints, on a body with cameras it is not rendering:

    no video: mujoco:so_arm100_cube has scene, wrist and is rendering none.
    --driver-arg render_cameras=wrist to record one.

Following that advice against a store holding 26 episodes recorded without cameras gave,
after sixty steps of motion:

    subscriber recorder died at step 0: ValueError: Feature mismatch in `frame`
    dictionary: Extra features: {'observation.images.wrist'}

A LeRobot dataset's feature schema is fixed when the dataset is created, and the recorder
is a bus subscriber — so it dies on the first frame, the run continues to completion
looking normal, and nothing is kept. The same happens in reverse: a store made *with* a
camera and a run rendering none.

Checked before the body moves, for the reason `bodies.py` already gives about physical
bodies: "Checked before construction, not after... touching the hardware in order to
decide whether to touch it." Deciding whether a run can be recorded does not require
running it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from rich.console import Console

from tendon.cli.observers import check_camera_schema, recorded_streams


class Skill:
    ref = "grasp/cube-sim"


def _store(tmp_path: Path, cameras: list[str]) -> Path:
    """A store whose `meta/info.json` declares these camera streams and nothing else."""
    directory = tmp_path / "grasp__cube-sim"
    (directory / "meta").mkdir(parents=True)
    features = {"observation.state": {}, "action": {}}
    for camera in cameras:
        features[f"observation.images.{camera}"] = {}
    (directory / "meta" / "info.json").write_text(json.dumps({"features": features}), "utf-8")
    return tmp_path


def _check(root: Path | None, cameras: tuple[str, ...]) -> str:
    console = Console(width=200, record=True)
    check_camera_schema(console, root, Skill(), cameras)
    return console.export_text()


# ----------------------------------------------------------------- it reads a store


def test_it_reads_the_streams_a_store_declares(tmp_path: Path) -> None:
    root = _store(tmp_path, ["wrist"])

    streams = recorded_streams(root / "grasp__cube-sim")

    assert streams is not None
    assert "observation.images.wrist" in streams


def test_an_unreadable_store_is_not_an_empty_one(tmp_path: Path) -> None:
    """None and [] lead to opposite conclusions, and only one of them is worth refusing
    a run over."""
    assert recorded_streams(tmp_path / "nothing-here") is None


# --------------------------------------------------------------------- it refuses


def test_adding_a_camera_to_a_store_without_one_is_refused(tmp_path: Path) -> None:
    root = _store(tmp_path, [])

    with pytest.raises(typer.Exit) as caught:
        _check(root, ("wrist",))

    assert caught.value.exit_code == 1


def test_the_refusal_names_both_sides_and_the_way_out(tmp_path: Path) -> None:
    """A message that only says "mismatch" sends somebody to read LeRobot's source."""
    root = _store(tmp_path, [])
    console = Console(width=200, record=True)

    with pytest.raises(typer.Exit):
        check_camera_schema(console, root, Skill(), ("wrist",))
    printed = console.export_text()

    assert "no cameras" in printed, "what the store has"
    assert "wrist" in printed, "what this run would write"
    assert "--store" in printed, "and what to do about it"


def test_dropping_a_camera_a_store_already_has_is_refused_too(tmp_path: Path) -> None:
    """The reverse direction. A run rendering nothing into a store made with a camera
    leaves that feature missing from every frame, which fails the same way."""
    root = _store(tmp_path, ["wrist"])

    with pytest.raises(typer.Exit):
        _check(root, ())


def test_a_different_camera_is_refused(tmp_path: Path) -> None:
    """Same count, different name — the shape that would slip past a length comparison."""
    root = _store(tmp_path, ["wrist"])

    with pytest.raises(typer.Exit):
        _check(root, ("scene",))


# --------------------------------------------------------------------- it allows


def test_a_matching_camera_set_is_allowed(tmp_path: Path) -> None:
    assert _check(_store(tmp_path, ["wrist"]), ("wrist",)) == ""


def test_no_cameras_on_either_side_is_allowed(tmp_path: Path) -> None:
    """The default path, and by far the most common one. A check that refused this would
    stop every ordinary run."""
    assert _check(_store(tmp_path, []), ()) == ""


def test_a_store_that_does_not_exist_yet_is_allowed(tmp_path: Path) -> None:
    """A new dataset takes whatever schema the first run gives it."""
    assert _check(tmp_path, ("wrist",)) == ""


def test_no_store_at_all_is_allowed() -> None:
    """`attach_recorder` returns `None` for the root when LeRobot is not installed, and
    nothing is being written, so there is nothing to disagree with."""
    assert _check(None, ("wrist",)) == ""


# ------------------------------------------------------------- and it is wired in


def test_both_commands_that_record_check_before_the_body_moves() -> None:
    """`run` and `eval` both attach a recorder. A check on one of them would leave the
    other with the original trap, and `eval` is the one that runs thirty episodes.

    Asserted against the order of calls in the source: the check has to come before
    `recorder.start`, because after it the first frame has already been refused.
    """
    import ast

    source = (Path(__file__).resolve().parents[2] / "src" / "tendon" / "cli" / "main.py").read_text(
        encoding="utf-8"
    )

    checks = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "check_camera_schema"
    ]
    starts = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "start"
    ]

    assert len(checks) == 2, f"{len(checks)} call sites check the schema; run and eval both record"
    assert starts, "no recorder is started anywhere, so the ordering below means nothing"
    assert min(checks) < min(starts), "the check has to happen before the first frame"
