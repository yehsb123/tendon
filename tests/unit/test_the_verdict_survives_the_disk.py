"""A field written on every append and read by nothing.

`EpisodeRecord.succeeded` was added so the v0.3 graph could not be read as "the policy
learned" when it might mean "the policy stopped trying". `append` writes `record.__dict__`,
so the verdict has been on disk since the field existed. `_read` rebuilt the record field
by field and did not name it, so every verdict ever logged came back `None`.

Measured on this machine's real log before the fix:

    on disk   : [False, False, False, False]
    read back : [None, None, None, None]

`tendon eval grasp/cube-sim` reported a real 0.0% success rate for those same episodes while
`tendon progress` printed "success was not measured on any of these episodes". Two commands
disagreeing about the one number the milestone turns on, and the one saying *unmeasured* was
the one people would have believed, because unmeasured is what this project has been saying
about itself all along.

A round trip is the test, not a call to `_read` with a hand-built dictionary. The bug was
exactly that the writer and the reader were written separately and never met.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tendon.services.progress import EpisodeRecord, append, history, now


def _record(**overrides) -> EpisodeRecord:
    fields = {
        "skill": "grasp/cube-sim",
        "body": "mujoco:so_arm100_cube",
        "episode_id": "abc",
        "ended_at": now(),
        "steps": 100,
        "interventions": 0,
        "corrections": 0,
        "corrections_known": 0,
    }
    fields.update(overrides)
    return EpisodeRecord(**fields)


@pytest.mark.parametrize("verdict", [True, False, None], ids=["succeeded", "failed", "unmeasured"])
def test_a_verdict_written_is_the_verdict_read(tmp_path: Path, verdict: bool | None) -> None:
    """All three, because the two that are not `None` are the ones that were being lost and
    the third is what they were being lost *into*."""
    append(tmp_path, "grasp/cube-sim", "mujoco:so_arm100_cube", _record(succeeded=verdict))

    (back,) = history(tmp_path, "grasp/cube-sim", "mujoco:so_arm100_cube")

    assert back.succeeded is verdict


def test_failed_does_not_come_back_as_unmeasured(tmp_path: Path) -> None:
    """The specific loss. Named on its own because it is the one that made a measured run
    look like an unmeasurable rig, which is the more comfortable of the two readings."""
    append(tmp_path, "s", "b", _record(skill="s", body="b", succeeded=False))

    (back,) = history(tmp_path, "s", "b")

    assert back.succeeded is False
    assert back.succeeded is not None, "nobody measured and it failed are opposite claims"


def test_a_whole_run_of_verdicts_survives_in_order(tmp_path: Path) -> None:
    """One record round-tripping is weaker than it looks: the loader could have read the
    first line's verdict and dropped the rest."""
    verdicts = [True, False, None, True, False]
    for i, verdict in enumerate(verdicts):
        append(
            tmp_path, "s", "b", _record(skill="s", body="b", episode_id=str(i), succeeded=verdict)
        )

    assert [r.succeeded for r in history(tmp_path, "s", "b")] == verdicts


def test_a_record_written_before_the_field_existed_is_unmeasured(tmp_path: Path) -> None:
    """Old lines have no `succeeded` key at all. Absent is unmeasured, which is true: nobody
    was judging when they were written."""
    path = tmp_path / "s__b.jsonl"
    path.write_text(
        json.dumps(
            {
                "skill": "s",
                "body": "b",
                "episode_id": "old",
                "ended_at": now(),
                "steps": 10,
                "interventions": 0,
                "corrections": 0,
                "corrections_known": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    (back,) = history(tmp_path, "s", "b")

    assert back.succeeded is None


@pytest.mark.parametrize("junk", [0, 1, "false", "true", [], {}])
def test_something_that_is_not_a_boolean_is_not_a_verdict(tmp_path: Path, junk: object) -> None:
    """`judge_result` returns `True`, `False` or `None` and nothing else, so a 0 or a
    "false" in this column did not come from it. Coercing one would put a made-up verdict on
    the axis the project is judged by; the truthful reading is that nobody measured."""
    path = tmp_path / "s__b.jsonl"
    path.write_text(
        json.dumps(
            {
                "skill": "s",
                "body": "b",
                "episode_id": "odd",
                "ended_at": now(),
                "steps": 10,
                "interventions": 0,
                "corrections": 0,
                "corrections_known": 0,
                "succeeded": junk,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    (back,) = history(tmp_path, "s", "b")

    assert back.succeeded is None


def test_every_field_the_writer_emits_is_a_field_the_reader_names() -> None:
    """The shape of the bug, rather than this instance of it.

    `append` writes `record.__dict__`, so adding a field to `EpisodeRecord` puts it on disk
    for free — and the reader is a hand-written constructor call that has to be updated by
    somebody who remembers. That asymmetry is what dropped this verdict, and it will drop
    the next field the same way.
    """
    import ast
    import dataclasses
    from pathlib import Path as _Path

    source = _Path(__file__).resolve().parents[2] / "src" / "tendon" / "services" / "progress.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    named: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "EpisodeRecord"
        ):
            named.update(kw.arg for kw in node.keywords if kw.arg)

    declared = {field.name for field in dataclasses.fields(EpisodeRecord)}

    assert declared <= named, (
        f"{sorted(declared - named)} are written by `append` and never read by `_read`; "
        f"they come back as their defaults"
    )
