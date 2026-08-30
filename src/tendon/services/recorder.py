"""Every run becomes an episode — design decision 1.

A bus subscriber, not a mode. There is no flag to enable it, because a recorder that can
be switched off will be switched off the first time it costs something.

Writes LeRobotDataset (parquet + mp4) plus a sidecar table holding what that format does
not model: confidence traces, interrupt spans, operator corrections, curation scores.
See ADR 0001.

The constraint that governs this module: recording must not measurably slow the control
loop. Frame writes are offloaded; the hot path only enqueues.
"""

from __future__ import annotations

from tendon.kernel.types import EpisodeMeta


class Recorder:
    async def start(self, skill: str, body_id: str) -> str:
        """Open an episode and return its episode_id."""
        raise NotImplementedError("v0.1")

    async def finish(self, episode_id: str, success: bool | None = None) -> EpisodeMeta:
        raise NotImplementedError("v0.1")
