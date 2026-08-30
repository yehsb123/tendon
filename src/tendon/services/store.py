"""Reading what has been recorded.

The recorder writes; this reads. Kept apart because listing episodes must work when the
recorder cannot even be imported — `lerobot` needs Python 3.12 and is an optional extra,
and "what have I recorded?" is a question someone should be able to ask on a machine that
cannot currently record anything.

So this reads the layout on disk rather than opening datasets through LeRobot. That is a
real constraint and it shows: episode counts come from `meta/info.json`, and a dataset
whose metadata is missing is reported as unreadable rather than skipped. Something on disk
that cannot be read is a more useful thing to know about than a shorter list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["DEFAULT_ROOT", "StoredDataset", "list_datasets"]

#: Where the recorder writes. Duplicated from `recorder.DEFAULT_ROOT` rather than imported,
#: because importing it would pull in LeRobot and make listing impossible exactly when it
#: is most wanted — on a machine that cannot record.
DEFAULT_ROOT = Path.home() / ".tendon" / "episodes"


@dataclass(frozen=True)
class StoredDataset:
    """One recorded dataset on disk."""

    #: Directory name. The recorder encodes a Hub-style ref by replacing "/" with "__".
    directory: str
    path: Path
    #: Episodes according to `meta/info.json`. None when the metadata could not be read.
    episodes: int | None
    #: Total bytes on disk.
    size_bytes: int
    modified: datetime
    #: Why this dataset could not be fully read, when it could not be.
    unreadable_because: str | None = None

    @property
    def ref(self) -> str:
        """The skill reference this was recorded under."""
        return self.directory.replace("__", "/")

    @property
    def readable(self) -> bool:
        return self.unreadable_because is None


def list_datasets(root: Path | None = None) -> tuple[StoredDataset, ...]:
    """Every dataset under the store, newest first.

    An empty store is not an error — it is the normal state before anything has run, and
    the caller says so better than an exception would.
    """
    base = root if root is not None else DEFAULT_ROOT
    if not base.is_dir():
        return ()

    found: list[StoredDataset] = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir():
            continue
        found.append(_read(directory))

    return tuple(sorted(found, key=lambda d: d.modified, reverse=True))


def _read(directory: Path) -> StoredDataset:
    size = _size_of(directory)
    modified = datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc)
    info = directory / "meta" / "info.json"

    if not info.is_file():
        return StoredDataset(
            directory=directory.name,
            path=directory,
            episodes=None,
            size_bytes=size,
            modified=modified,
            unreadable_because="no meta/info.json — not a LeRobotDataset, or a partial write",
        )

    try:
        meta = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return StoredDataset(
            directory=directory.name,
            path=directory,
            episodes=None,
            size_bytes=size,
            modified=modified,
            unreadable_because=f"meta/info.json could not be read: {exc}",
        )

    episodes = meta.get("total_episodes")
    return StoredDataset(
        directory=directory.name,
        path=directory,
        episodes=int(episodes) if isinstance(episodes, int) else None,
        size_bytes=size,
        modified=modified,
        unreadable_because=(
            None if isinstance(episodes, int) else "meta/info.json has no total_episodes"
        ),
    )


def _size_of(directory: Path) -> int:
    """Bytes under a directory.

    Unreadable entries are counted as zero rather than raising: a permission error on one
    file should not stop the whole listing, and an approximate size is more useful than a
    stack trace.
    """
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def human_size(num_bytes: int) -> str:
    """Bytes as something a person reads at a glance."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
