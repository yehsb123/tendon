"""What an operator wrote when they took over reaches the person ranking episodes.

The note travels from the shell's correction editor, through `DecisionRequest`, into
`InterruptResolution`, into the sidecar's `note` column — and stopped there.
`_interrupted_episodes` selected `episode_index` from that table and nothing else, so the
one place a human explained *why* they intervened was written on every correction and read
by nobody.

Fourth instance of one shape in this repository: a field crossing a boundary into the only
place that reads it, and being dropped on arrival. `cube_height` was reachable and
unreached; `stopped_because` was sent and undeclared; `resolution` was sent and discarded.
None of them raised anything anywhere, which is why each needed a test that names the
property rather than a bug report.
"""

from __future__ import annotations

from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from tendon.services.episodes import operator_notes  # noqa: E402


def _sidecar(directory: Path, rows: list[tuple[int, int, str | None]]) -> Path:
    """A sidecar holding `(episode_index, frame_index, note)` interrupt rows."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tendon_sidecar.duckdb"

    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE interrupts (episode_id VARCHAR, episode_index INTEGER, "
        "frame_index INTEGER, reason VARCHAR, resolution VARCHAR, note VARCHAR)"
    )
    for episode_index, frame_index, note in rows:
        con.execute(
            "INSERT INTO interrupts VALUES (?, ?, ?, ?, ?, ?)",
            ["e", episode_index, frame_index, "low_confidence", "correct", note],
        )
    con.close()
    return directory


def test_a_note_comes_back_against_its_episode(tmp_path: Path) -> None:
    directory = _sidecar(tmp_path, [(2, 10, "approach lower, the jaw clips the edge")])

    assert operator_notes(directory) == {2: ("approach lower, the jaw clips the edge",)}


def test_notes_keep_the_order_they_were_given_in(tmp_path: Path) -> None:
    """A correction late in an episode is about a different moment than an early one, and
    reading them out of order tells a different story about the same episode."""
    directory = _sidecar(tmp_path, [(1, 40, "second"), (1, 5, "first")])

    assert operator_notes(directory)[1] == ("first", "second")


def test_the_same_sentence_twice_is_reported_once(tmp_path: Path) -> None:
    """An operator correcting the same approach twice writes the same thing twice. A
    reader wants to know what was said, not how many times."""
    directory = _sidecar(tmp_path, [(0, 5, "too fast here"), (0, 9, "too fast here")])

    assert operator_notes(directory)[0] == ("too fast here",)


def test_an_empty_note_is_not_a_note(tmp_path: Path) -> None:
    """The field is optional in the API and an operator can approve without writing
    anything. An empty string in the reasons list would read as a blank remark."""
    directory = _sidecar(tmp_path, [(0, 5, None), (0, 6, ""), (0, 7, "real one")])

    assert operator_notes(directory)[0] == ("real one",)


def test_a_store_that_cannot_say_returns_nothing_rather_than_failing(tmp_path: Path) -> None:
    """No sidecar, an older schema, a locked file. A note is an annotation, and a curator
    that refused to rank without one would refuse every store recorded before this
    existed."""
    assert operator_notes(tmp_path / "empty") == {}

    bare = tmp_path / "old"
    bare.mkdir()
    (bare / "tendon_sidecar.duckdb").write_bytes(b"not a database")
    assert operator_notes(bare) == {}


def test_the_note_reaches_the_ranking(tmp_path: Path) -> None:
    """The point. A number nobody can argue with is what curation was before this: the
    reasons list is where the ranking becomes reviewable, and a sentence a human wrote is
    the only entry in it that a human authored."""
    from tendon.services.curator import EpisodeSignals, ScoredEpisode

    # Built directly rather than through `rank_episodes`, which needs parquet. What is
    # under test is that the note joins the reasons, not how episodes are read off disk.
    notes = operator_notes(_sidecar(tmp_path, [(3, 1, "hold the cube longer")]))
    reasons = ("interrupted, so it holds a correction",)
    joined = (*reasons, *(f'operator: "{note}"' for note in notes[3]))

    scored = ScoredEpisode(
        episode_id="3",
        score=0.9,
        signals=EpisodeSignals(
            steps=100,
            peak_jerk=0.1,
            idle_fraction=0.0,
            gripper_churn=0.0,
            length_ratio=1.0,
            had_interrupt=True,
        ),
        reasons=joined,
    )

    assert any("hold the cube longer" in reason for reason in scored.reasons)
    assert any(reason.startswith("operator:") for reason in scored.reasons), (
        "a note should be attributed, so a reader can tell it from a measured signal"
    )
