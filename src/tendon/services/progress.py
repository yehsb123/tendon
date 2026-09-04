"""The graph this project exists to produce, as data.

`docs/roadmap.md` says v0.3 is done when one graph exists:

    x-axis: cumulative human corrections.  y-axis: intervention rate.
    The line goes down.

That graph has been produced twice — by `examples/04_improve`, a script, and by
`tests/integration/test_shell_loop_closes.py`, which drives the real interface and proves
the fall is caused by the teaching. Neither leaves anything behind. Nothing in the running
system records how often it asked, so an operator who spends a week correcting a policy has
no way to see whether it is working.

This is the append-only log that fixes that: one line per finished episode, in order.

## Why not from the episode store

The episodes are all there, and the interrupts are in each sidecar — but the sidecar keys
them by the recorder's uuid while the parquet numbers episodes from zero, with no column
joining the two (docs/collaboration.md). Until that lands, "how many interrupts did episode
7 have" cannot be answered from the store at all. It can be answered here, because this is
written at the moment the episode ends and knows exactly which one it was.

When that column arrives this file becomes derived data that could be rebuilt, and it will
still be worth keeping: reading a hundred parquet files to draw a line is not what a view
should do on every load.

## What one line is

Episode-level, not step-level. The whole file for a thousand episodes is well under a
megabyte, and nothing here is written during an episode — this is one append when a run
finishes, at human timescale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["DEFAULT_PROGRESS_ROOT", "EpisodeRecord", "append", "history", "progress_path"]

#: Beside the memory and the episodes, not inside either. This is a log of what happened;
#: the memory is what is currently known and the store is the data itself.
DEFAULT_PROGRESS_ROOT = Path.home() / ".tendon" / "progress"

#: Same reasoning as `memory_store`: identifiers come from drivers and skill authors, and
#: `mujoco:so_arm100_cube` contains a character a Windows filename cannot.
_UNSAFE = ':<>"|?*\\/'


@dataclass(frozen=True)
class EpisodeRecord:
    """One finished episode, as the graph needs it.

    Carries its own `skill` and `body` even though the filename encodes both. The filename
    is sanitised — `grasp/cube-sim` becomes `grasp_cube-sim` — and recovering the original
    from it means guessing which underscore used to be a slash, which is wrong the moment a
    skill has an underscore in its name. A file that describes itself needs no guessing.
    """

    skill: str
    body: str
    episode_id: str
    ended_at: str
    steps: int
    interventions: int
    corrections: int
    #: Corrections held for this skill and body *after* this episode. The x-axis: it only
    #: ever grows, and it is what the intervention rate is plotted against.
    corrections_known: int
    #: Whether the episode achieved what the skill declares as success. None when nobody
    #: could tell — the skill names no criteria, or the body does not report the quantity
    #: they need. Three states, not two, because "failed" and "unmeasured" are opposite
    #: claims and a boolean would have to lie about one of them.
    #:
    #: Recorded because the y-axis alone is ambiguous. **A policy that stops asking for
    #: help because it stopped trying draws exactly the same falling line as one that
    #: learned.** The graph is the whole claim of this project and nothing distinguished
    #: those two readings of it: `examples/04_improve` prints PASS on the fall alone.
    succeeded: bool | None = None


def progress_path(root: Path, skill: str, body: str) -> Path:
    safe = "".join("_" if c in _UNSAFE else c for c in f"{skill}__{body}")
    return root / f"{safe}.jsonl"


def append(root: Path, skill: str, body: str, record: EpisodeRecord) -> None:
    """Add one episode to the log.

    JSON lines rather than a single JSON document, so appending is an append. Rewriting a
    growing array on every episode would make the cost of finishing a run scale with how
    long somebody has been working, which is precisely backwards.
    """
    path = progress_path(root, skill, body)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.__dict__) + "\n")


def history(root: Path, skill: str, body: str) -> tuple[EpisodeRecord, ...]:
    """Every episode recorded for this skill and body, oldest first.

    A malformed line is skipped rather than fatal. The file is appended to by a running
    system that can be killed mid-write, so a truncated last line is an ordinary thing to
    find, and losing a week of history over one of them would be absurd.
    """
    return _read(progress_path(root, skill, body))


def _read(path: Path) -> tuple[EpisodeRecord, ...]:
    if not path.is_file():
        return ()

    found: list[EpisodeRecord] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            found.append(
                EpisodeRecord(
                    skill=str(raw["skill"]),
                    body=str(raw["body"]),
                    episode_id=str(raw["episode_id"]),
                    ended_at=str(raw["ended_at"]),
                    steps=int(raw["steps"]),
                    interventions=int(raw["interventions"]),
                    corrections=int(raw["corrections"]),
                    corrections_known=int(raw["corrections_known"]),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue

    return tuple(found)


def logs(root: Path) -> tuple[tuple[str, str, tuple[EpisodeRecord, ...]], ...]:
    """Every log under a root, as (skill, body, records).

    Names are read from the records rather than from the filenames, so nothing here has to
    reverse the sanitising that produced them.
    """
    if not root.is_dir():
        return ()

    found = []
    for path in sorted(root.glob("*.jsonl")):
        records = _read(path)
        if records:
            found.append((records[0].skill, records[0].body, records))

    return tuple(found)


def now() -> str:
    """Timestamps in UTC, always. A log read on a different machine from the one that
    wrote it should not need to know where it was written."""
    return datetime.now(tz=timezone.utc).isoformat()


def rate_curve(
    records: tuple[EpisodeRecord, ...], *, window: int = 10
) -> tuple[tuple[int, float], ...]:
    """The line itself: cumulative corrections against a trailing intervention rate.

    A trailing window rather than a cumulative average. A cumulative rate is dominated by
    the early episodes and keeps falling long after improvement has stopped, which makes
    the graph look right for the wrong reason — the same point
    `tests/integration/test_improve_example.py` holds the example to.

    Returns nothing until a full window exists. A rate over three episodes is not a rate,
    and drawing one would invite somebody to read a trend off noise.
    """
    if len(records) < window:
        return ()

    points: list[tuple[int, float]] = []
    for index in range(window - 1, len(records)):
        recent = records[index - window + 1 : index + 1]
        interrupted = sum(1 for r in recent if r.interventions > 0)
        points.append((records[index].corrections_known, interrupted / window))

    return tuple(points)
