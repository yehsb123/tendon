"""Keeping what an operator taught, across restarts.

The correction memory lived for as long as `tendon serve` did. Somebody could spend an
afternoon teaching a policy, restart the runtime, and find it asking every one of the same
questions again. That is the loop opening back up between sessions rather than during them.

## Why this is not in the episode sidecar

`recorder.note_interrupt` writes what happened in an episode: that control was handed over,
and what was decided. That is history, and history is immutable — it describes a run that
is over.

The correction memory is not history. It is what the system currently knows, used live to
decide whether to ask. It could in principle be rebuilt from history, and one day should
be; but a rebuild needs a column joining sidecar rows to episodes that the recorder does
not write yet (docs/collaboration.md), and more importantly the two have different
lifetimes. An episode is never edited. A memory is appended to every time somebody
corrects something.

So they are separate files, and this one says plainly that it is derived state: deleting it
loses what was taught and nothing else, and the episodes it came from are still there.

## When it is written

On each correction, not at the end of an episode. Corrections arrive when a human decides
something, which is nowhere near control rate, and an episode that crashes after a
correction should not throw that correction away — losing the thing a person just did is
the failure this module exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tendon.services.adaptive import CorrectionMemory

__all__ = ["DEFAULT_MEMORY_ROOT", "load_memory", "save_memory", "memory_path"]

#: Beside the episode store rather than inside it. What is in here is derived from what is
#: in there, and mixing the two would invite somebody to back up one and not the other.
DEFAULT_MEMORY_ROOT = Path.home() / ".tendon" / "memory"

#: Bumped when the on-disk shape changes. A memory written by a newer tendon is skipped
#: rather than misread: a correction recalled from a misparsed file is a motion nobody
#: chose, which is worse than starting empty.
_FORMAT = 1


#: Characters a body id or skill ref may contain that a filesystem will not. `body_id` is
#: `mujoco:so_arm100_cube` — the colon is legal in the identifier and illegal in a Windows
#: filename, which is how the first version of this module wrote nothing at all and said
#: nothing about it.
_UNSAFE = ':<>"|?*\\/'


def _safe(part: str) -> str:
    return "".join("_" if c in _UNSAFE else c for c in part)


def memory_path(root: Path, skill: str, body: str) -> Path:
    """Where one skill's memory for one body lives.

    Keyed on both because a correction is a joint-space position: it means nothing on a
    body with different kinematics, and nothing about a different task.

    Both parts are flattened into one filename rather than nested, so the directory can be
    listed and read at a glance, and the identifiers are sanitised rather than trusted:
    they are chosen by drivers and skill authors, not by this module.
    """
    return root / f"{_safe(skill)}__{_safe(body)}.json"


def load_memory(root: Path, skill: str, body: str) -> CorrectionMemory:
    """Read a memory, or return an empty one.

    Never raises. A memory that cannot be read is a reason to start empty and ask more
    often, which is the safe direction: the failure mode of an unreadable file must not be
    a policy acting on corrections it cannot actually parse.
    """
    from tendon.kernel.types import Intent
    from tendon.services.adaptive import CorrectionMemory

    path = memory_path(root, skill, body)
    if not path.is_file():
        return CorrectionMemory()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CorrectionMemory()

    if not isinstance(raw, dict) or raw.get("format") != _FORMAT:
        return CorrectionMemory()

    memory = CorrectionMemory(radius=float(raw.get("radius", CorrectionMemory.radius)))
    for entry in raw.get("entries", []):
        try:
            positions = [float(v) for v in entry["positions"]]
            correction = Intent.model_validate(entry["correction"])
        except Exception:  # noqa: BLE001 - one bad entry must not lose the rest
            continue
        memory.entries.append((positions, correction))

    return memory


def save_memory(root: Path, skill: str, body: str, memory: CorrectionMemory) -> None:
    """Write a memory, atomically.

    Written to a temporary file and moved into place, because the alternative is a
    truncated file where a memory used to be — and `load_memory` would then correctly
    decide to start empty, silently discarding everything taught so far.
    """
    path = memory_path(root, skill, body)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "format": _FORMAT,
        "skill": skill,
        "body": body,
        "radius": memory.radius,
        "entries": [
            {"positions": list(positions), "correction": correction.model_dump(mode="json")}
            for positions, correction in memory.entries
        ],
    }

    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    temporary.replace(path)
