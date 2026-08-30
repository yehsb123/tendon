"""Reading recorded episodes back, without the stack that wrote them.

`store.py` answers "what have I recorded?" from the directory layout. This answers "what
happened in it?" from the parquet, and it is a separate module for the same reason: the
question outlives the ability to record.

## Why duckdb and not LeRobot

`tendon curate` reported for months that it was waiting on "reading recorded episodes back,
which needs the [robot] extra". That turned out to be an assumption nobody had checked. A
LeRobotDataset on disk is parquet with an ordinary schema — `action`, `observation.state`,
`episode_index`, `frame_index` — and duckdb, already a dependency for the sidecar, reads it
directly.

So curation runs on a machine that cannot record: no LeRobot, no torch, no simulator. That
matters more than it sounds. Curation is the step where somebody decides what is worth
training on, and it is exactly the step you want to run on a laptop against data collected
somewhere else.

The frames themselves — video — are not read here. Nothing that scores an episode looks at
pixels yet, and decoding video to compute jerk would make a cheap question expensive.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tendon.kernel.types import Action, ActionSpace

__all__ = ["StoredEpisode", "EpisodeReadError", "read_episodes"]


class EpisodeReadError(RuntimeError):
    """A dataset is on disk and cannot be read as episodes."""


@dataclass(frozen=True)
class StoredEpisode:
    """One recorded episode, as much of it as scoring needs."""

    #: `episode_index` from the dataset. Stable within a dataset; not a uuid.
    episode_id: str
    actions: tuple[Action, ...]
    #: Control period the episode was recorded at [s], from `meta/info.json`.
    dt_s: float
    #: Whether an operator was handed control during it, or None when the store cannot
    #: say. See `_interrupted_episodes`: a sidecar with no interrupt rows proves no episode
    #: was interrupted, but one *with* rows cannot yet say which episode they belong to.
    #: None rather than False, because "nobody was interrupted" and "nobody can tell" lead
    #: a curator to opposite conclusions about what is worth keeping.
    had_interrupt: bool | None = False


def read_episodes(directory: Path, *, gripper: bool | None = None) -> tuple[StoredEpisode, ...]:
    """Every episode in one dataset directory, in recorded order.

    Args:
        directory: A dataset directory under the store, e.g. `~/.tendon/episodes/
            grasp__cube-sim`.
        gripper: Whether the last action channel is a jaw. Read from the dataset's own
            feature names when None, because guessing wrong turns a gripper into a sixth
            joint and every jerk measurement with it.
    """
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - duckdb is a hard dependency
        raise EpisodeReadError("duckdb is required to read episodes back") from exc

    files = sorted(str(p) for p in (directory / "data").rglob("*.parquet"))
    if not files:
        raise EpisodeReadError(f"no episode data under {directory / 'data'}")

    info = _info(directory)
    fps = info.get("fps")
    if not isinstance(fps, int | float) or fps <= 0:
        raise EpisodeReadError(f"{directory / 'meta' / 'info.json'} has no usable fps")
    dt_s = 1.0 / float(fps)

    has_gripper = _has_gripper(info) if gripper is None else gripper
    interrupted = _interrupted_episodes(directory)

    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT episode_index, action FROM read_parquet(?) ORDER BY episode_index, frame_index",
            [files],
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - duckdb raises its own hierarchy
        raise EpisodeReadError(f"could not read {directory}: {exc}") from exc
    finally:
        con.close()

    grouped: dict[int, list[Action]] = {}
    for episode_index, values in rows:
        grouped.setdefault(int(episode_index), []).append(_action(values, has_gripper))

    return tuple(
        StoredEpisode(
            episode_id=str(index),
            actions=tuple(actions),
            dt_s=dt_s,
            had_interrupt=interrupted,
        )
        for index, actions in sorted(grouped.items())
    )


def _action(values: Any, has_gripper: bool) -> Action:
    numbers = [float(v) for v in values]
    if has_gripper and numbers:
        return Action(
            space=ActionSpace.JOINT_POSITION,
            values=numbers[:-1],
            # Clamped rather than trusted: the column is float32 on disk and a value a
            # hair outside [0, 1] would fail validation on data that is otherwise fine.
            gripper=min(1.0, max(0.0, numbers[-1])),
        )
    return Action(space=ActionSpace.JOINT_POSITION, values=numbers)


def _info(directory: Path) -> dict[str, Any]:
    import json

    path = directory / "meta" / "info.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EpisodeReadError(f"{path} could not be read: {exc}") from exc

    if not isinstance(loaded, dict):
        raise EpisodeReadError(f"{path} does not contain a mapping")
    return loaded


def _has_gripper(info: dict[str, Any]) -> bool:
    """Whether the action's last channel is a jaw, according to the dataset itself."""
    features = info.get("features")
    if not isinstance(features, dict):
        return False

    action = features.get("action")
    names = action.get("names") if isinstance(action, dict) else None
    return isinstance(names, list) and bool(names) and names[-1] == "gripper"


def _interrupted_episodes(directory: Path) -> bool | None:
    """Whether the episodes in this dataset were interrupted, when that is knowable.

    Returns False when the sidecar exists and holds no interrupt rows at all — that does
    prove no episode was interrupted. Returns None otherwise.

    None covers two cases and both are genuinely unknown. There is no sidecar, so nothing
    was written down. Or there are interrupt rows and **they cannot be attributed**: the
    sidecar keys them by the recorder's episode uuid and the parquet numbers episodes from
    zero, with no column joining the two.

    The tempting fix is to match them by write order. It reads as reasonable and is a
    guess: it is right only for a store written by one process in one sequence, and wrong
    silently for anything else — an interrupt episode promoted into a training set is
    exactly the mistake curation exists to avoid. Track A owns `recorder.py`; one column
    settles it (docs/collaboration.md).
    """
    sidecar = directory / "tendon_sidecar.duckdb"
    if not sidecar.is_file():
        return None

    try:
        import duckdb

        con = duckdb.connect(str(sidecar), read_only=True)
    except Exception:  # noqa: BLE001 - a locked or corrupt sidecar is not fatal here
        return None

    try:
        row = con.execute("SELECT count(*) FROM interrupts").fetchone()
    except Exception:  # noqa: BLE001 - the table may not exist on an older dataset
        return None
    finally:
        con.close()

    # `fetchone` is typed as optional and a count query will always return a row, but
    # unpacking on that assumption is how a query that changes shape later becomes a
    # TypeError instead of an unknown.
    if row is None:
        return None
    return False if row[0] == 0 else None
