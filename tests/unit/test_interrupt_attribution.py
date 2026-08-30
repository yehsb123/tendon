"""Which episodes an operator was handed control in.

The curator values interrupt episodes above every other kind, because demonstration data
almost never contains recovery from failure and those are the only recordings of it. For
most of this project's life the store could not say which episodes they were: the sidecar
keyed interrupts by the recorder's episode uuid, the parquet numbered episodes from zero,
and no column joined them.

Matching by write order was available the whole time and reads as perfectly reasonable. It
is a guess — right only for a store written by one process in one sequence, silently wrong
otherwise — and an interrupt episode promoted into a training set on a guess is the exact
mistake curation exists to prevent. So `read_episodes` reported `None` and `tendon curate`
said out loud that nothing had been promoted, for as long as that was the truth.

The recorder now writes `episode_index` beside each interrupt, so the question is
answerable and this file is about answering it precisely: a set when the store can say, an
empty set when it can prove nobody was interrupted, and `None` when it genuinely cannot
tell. Those are three different answers and collapsing any two of them loses something.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tendon.services.episodes import _interrupted_episodes

duckdb = pytest.importorskip("duckdb")


def make_sidecar(directory: Path, rows: list[tuple[str, int | None]]) -> None:
    """A sidecar holding `(resolution, episode_index)` interrupt rows.

    Built directly rather than by recording, so the three cases below can be produced
    exactly — including the one that no longer occurs and still has to be handled: a
    dataset recorded before the column existed.
    """
    directory.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(directory / "tendon_sidecar.duckdb"))
    try:
        con.execute(
            "CREATE TABLE interrupts ("
            "episode_id VARCHAR, episode_index BIGINT, frame_index BIGINT, "
            "reason VARCHAR, resolution VARCHAR, note VARCHAR, corrected BOOLEAN)"
        )
        for resolution, index in rows:
            con.execute(
                "INSERT INTO interrupts "
                "(episode_id, episode_index, frame_index, reason, resolution, note, corrected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ["abc", index, 12, "low_confidence", resolution, None, True],
            )
    finally:
        con.close()


# ------------------------------------------------------------------- it can say


def test_interrupts_are_attributed_to_their_episodes(tmp_path: Path) -> None:
    make_sidecar(tmp_path, [("corrected", 0), ("corrected", 3)])

    assert _interrupted_episodes(tmp_path) == {0, 3}


def test_repeated_interrupts_in_one_episode_count_once(tmp_path: Path) -> None:
    """The question is which episodes were interrupted, not how often. An episode
    interrupted twice is not twice as interrupted."""
    make_sidecar(tmp_path, [("corrected", 2), ("approved", 2), ("aborted", 2)])

    assert _interrupted_episodes(tmp_path) == {2}


# ------------------------------------------------------- and when it can prove none


def test_a_sidecar_with_no_interrupts_proves_there_were_none(tmp_path: Path) -> None:
    """An empty set, not `None`. The sidecar is there and holds nothing, which is a fact
    about the run rather than an absence of information."""
    make_sidecar(tmp_path, [])

    assert _interrupted_episodes(tmp_path) == set()


# -------------------------------------------------------------- and when it cannot


def test_rows_without_an_episode_index_are_unknown_not_none_interrupted(tmp_path: Path) -> None:
    """A dataset recorded before the column existed.

    Inventing an answer for it now would be the same guess arriving late, and it would be
    wrong in the direction that puts recovery-from-failure episodes into a training set
    they do not belong in.
    """
    make_sidecar(tmp_path, [("corrected", None), ("approved", None)])

    assert _interrupted_episodes(tmp_path) is None


def test_no_sidecar_at_all_is_unknown(tmp_path: Path) -> None:
    assert _interrupted_episodes(tmp_path) is None


def test_a_sidecar_without_the_table_is_unknown(tmp_path: Path) -> None:
    """Something else's duckdb file, or one from a version that stored nothing here."""
    con = duckdb.connect(str(tmp_path / "tendon_sidecar.duckdb"))
    try:
        con.execute("CREATE TABLE something_else (x INTEGER)")
    finally:
        con.close()

    assert _interrupted_episodes(tmp_path) is None


def test_an_unreadable_sidecar_is_unknown(tmp_path: Path) -> None:
    """Not fatal. Curation still has every episode's actions to score; what it loses is
    the promotion, and it says so."""
    (tmp_path / "tendon_sidecar.duckdb").write_text("not a database", encoding="utf-8")

    assert _interrupted_episodes(tmp_path) is None


# ---------------------------------------------------- and the three stay distinct


def test_the_three_answers_are_not_collapsed(tmp_path: Path) -> None:
    """`set()` and `None` are both falsy, so anything testing them with a plain `if` reads
    "nobody was interrupted" and "nobody can tell" as the same thing. They lead a curator
    to opposite conclusions about what is worth keeping."""
    empty = tmp_path / "empty"
    unknown = tmp_path / "unknown"
    make_sidecar(empty, [])
    make_sidecar(unknown, [("corrected", None)])

    assert _interrupted_episodes(empty) is not None
    assert _interrupted_episodes(unknown) is None


def test_the_fixture_writes_what_the_recorder_writes() -> None:
    """This file builds its own sidecars, so it would keep passing if the recorder's
    columns were renamed. Checked against the recorder's own DDL instead of trusting the
    shape written above."""
    source = (Path(__file__).resolve().parents[2] / "src/tendon/services/recorder.py").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS interrupts" in source
    assert "episode_index" in source
