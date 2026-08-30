"""What works here, and what does not.

The first command anyone runs, and the one that decides whether the next hour is spent on
the project or on an install. So it reports what is missing *and what that costs* — a
checklist of green ticks tells you nothing about whether you can start.

Every check is read-only and none of them touches hardware. Running `doctor` must be safe
on a machine with a robot attached.
"""

from __future__ import annotations

import contextlib
import importlib.util
import platform
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = ["Check", "Status", "run_checks"]

#: Rough working space for episodes before anything needs pruning.
_RECOMMENDED_FREE_GB = 20.0


class Status(str, Enum):
    OK = "ok"
    #: Works, but something is degraded or missing that limits what can be done.
    LIMITED = "limited"
    #: Cannot proceed for the thing this check covers.
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    #: What to do about it. Empty when nothing is needed.
    remedy: str = ""


def _installed(module: str) -> bool:
    """Whether a module can be imported without importing it.

    Deliberately does not import: importing torch takes seconds and loads CUDA, which is
    not something a diagnostic should do to find out whether it exists.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _python() -> Check:
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info[2]}"

    if (major, minor) < (3, 10):
        return Check(
            "python",
            Status.BLOCKED,
            f"{version} — tendon needs 3.10 or newer",
            "install a newer Python",
        )
    if (major, minor) < (3, 12):
        return Check(
            "python",
            Status.LIMITED,
            f"{version} — the kernel and MuJoCo driver work; the lerobot extra does not",
            "LeRobot 0.6 requires 3.12+. Use 3.12 if you need the robot extra",
        )
    return Check("python", Status.OK, f"{version} on {platform.system()}")


def _simulation() -> Check:
    if _installed("mujoco"):
        return Check("simulation", Status.OK, "mujoco available")
    return Check(
        "simulation",
        Status.BLOCKED,
        "mujoco not installed — no body to run on",
        'pip install -e ".[sim]"',
    )


def _drivers() -> Check:
    """Which bodies are registered.

    Imports the driver package rather than probing, since registration happens on import
    and there is no other way to know.
    """
    from tendon.drivers import base

    # Importing is how a driver registers itself, so absence is expected rather than
    # exceptional: a machine without the sim extra simply has no mujoco body.
    with contextlib.suppress(ImportError):
        import tendon.drivers.mujoco  # noqa: F401

    available = base.available()
    if not available:
        return Check(
            "drivers",
            Status.BLOCKED,
            "no bodies registered",
            'install a driver extra, e.g. pip install -e ".[sim]"',
        )
    return Check("drivers", Status.OK, ", ".join(available))


def _training() -> Check:
    if not _installed("torch"):
        return Check(
            "training",
            Status.LIMITED,
            "torch not installed — running and recording work, fine-tuning does not",
            'pip install -e ".[train]"  (needed from v0.3)',
        )

    # Import is unavoidable to ask about CUDA, and by this point torch is present, so the
    # cost is already accepted by whoever installed it.
    import torch

    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        return Check("training", Status.OK, f"torch with CUDA — {name}")
    return Check(
        "training",
        Status.LIMITED,
        "torch present but no CUDA device — LoRA fine-tuning will not close overnight",
        "a consumer GPU is enough; CPU training is not a practical loop",
    )


def _datasets() -> Check:
    if _installed("lerobot"):
        return Check("datasets", Status.OK, "lerobot available")
    return Check(
        "datasets",
        Status.LIMITED,
        "lerobot not installed — episodes cannot be written in LeRobotDataset format",
        'pip install -e ".[robot]"  (needs Python 3.12+)',
    )


def _visualisation() -> Check:
    if _installed("rerun_sdk") or _installed("rerun"):
        return Check("visualisation", Status.OK, "rerun available")
    return Check(
        "visualisation",
        Status.LIMITED,
        "rerun not installed — the shell will have no scene view",
        'pip install -e ".[view]"',
    )


def _storage(root: Path | None = None) -> Check:
    """Free space where episodes will land.

    Recording is continuous by design, so running out of disk is a normal failure mode
    rather than an exotic one, and it is better found now than at step 4000.
    """
    path = root or Path.cwd()
    try:
        free_gb = shutil.disk_usage(path).free / 1024**3
    except OSError as exc:
        return Check("storage", Status.LIMITED, f"could not measure free space: {exc}")

    if free_gb < 1.0:
        return Check(
            "storage",
            Status.BLOCKED,
            f"{free_gb:.1f} GB free — recording will fail almost immediately",
            "free some space before running",
        )
    if free_gb < _RECOMMENDED_FREE_GB:
        return Check(
            "storage",
            Status.LIMITED,
            f"{free_gb:.1f} GB free — episodes accumulate continuously",
            f"{_RECOMMENDED_FREE_GB:.0f} GB is a comfortable working margin",
        )
    return Check("storage", Status.OK, f"{free_gb:.1f} GB free")


def _hub() -> Check:
    """Whether skills could be installed or published.

    Not needed until v0.4, so its absence is never blocking.
    """
    if not _installed("huggingface_hub"):
        return Check(
            "hub",
            Status.LIMITED,
            "huggingface_hub not installed — skill install and publish unavailable",
            "not needed before v0.4",
        )

    from huggingface_hub import get_token

    if get_token():
        return Check("hub", Status.OK, "authenticated")
    return Check(
        "hub",
        Status.LIMITED,
        "not authenticated — public skills can be installed, publishing cannot",
        "huggingface-cli login",
    )


def run_checks() -> list[Check]:
    """Every check, in the order a reader should think about them.

    Ordered by what blocks what: an interpreter that cannot run the code makes the rest
    moot, and the Hub matters only once everything else works.
    """
    checks = [_python(), _simulation(), _drivers(), _storage()]
    checks.extend([_datasets(), _training(), _visualisation(), _hub()])
    return checks


def summarise(checks: list[Check]) -> tuple[Status, str]:
    """One line saying whether work can start, and on what.

    A list of ticks and crosses leaves the reader to work out the consequence. This says
    it, because the consequence is the only thing they actually wanted.
    """
    blocked = [c for c in checks if c.status is Status.BLOCKED]
    limited = [c for c in checks if c.status is Status.LIMITED]

    if blocked:
        return Status.BLOCKED, (
            f"{len(blocked)} blocking issue(s): "
            + ", ".join(c.name for c in blocked)
            + ". Nothing can run until these are fixed."
        )
    if limited:
        return Status.LIMITED, (
            "You can run and record episodes. Not yet available: "
            + ", ".join(c.name for c in limited)
            + "."
        )
    return Status.OK, "Everything is available, including training and publishing."
