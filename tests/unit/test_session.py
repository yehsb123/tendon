"""A live episode, driven from outside.

The scheduler is synchronous; the API is asyncio. These tests cover the bridge between
them, and specifically the parts that would fail as a hang rather than an error — which is
the failure mode that costs the most time to diagnose.
"""

from __future__ import annotations

import queue
import threading
import time

import pytest

from tendon.api.session import EpisodeSession, ShellHandler
from tendon.kernel.types import (
    Action,
    ActionSpace,
    Confidence,
    ConfidenceSource,
    Intent,
    InterruptContext,
    InterruptReason,
    InterruptResolution,
    Observation,
    Proprioception,
    Resolution,
)


def an_intent() -> Intent:
    return Intent(
        horizon_s=0.1,
        actions=(Action(space=ActionSpace.JOINT_POSITION, values=[0.0, 0.0]),),
        confidence=Confidence(score=0.2, source=ConfidenceSource.CHUNK_VARIANCE),
    )


def a_context(step: int = 3) -> InterruptContext:
    return InterruptContext(
        episode_id="ep-1",
        step=step,
        reason=InterruptReason.LOW_CONFIDENCE,
        intent=an_intent(),
        observation=Observation(step=step, proprio=Proprioception(joint_positions=[0.0, 0.0])),
    )


# -------------------------------------------------------------------------- handler


def test_resolve_blocks_until_someone_decides() -> None:
    """The blocking is the point.

    A handler that returned immediately with a default would be approving on the
    operator's behalf, which is the whole thing this design exists to prevent.
    """
    handler = ShellHandler(queue.Queue(), timeout_s=5.0)
    result: list[InterruptResolution] = []

    def ask() -> None:
        result.append(handler.resolve(a_context()))

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()

    # Give the worker time to reach the wait, then confirm it has not answered itself.
    time.sleep(0.05)
    assert not result, "resolve returned without a decision"

    assert handler.decide(InterruptResolution(resolution=Resolution.APPROVED))
    worker.join(timeout=2.0)

    assert result and result[0].resolution is Resolution.APPROVED


def test_a_timeout_aborts_rather_than_approving() -> None:
    """Nobody answered, so nobody approved. A body must not resume because a person
    walked away from the screen."""
    handler = ShellHandler(queue.Queue(), timeout_s=0.05)
    resolution = handler.resolve(a_context())

    assert resolution.resolution is Resolution.ABORTED
    assert resolution.note is not None and "no operator decision" in resolution.note


def test_the_interrupt_is_published_for_viewers() -> None:
    events: queue.Queue = queue.Queue()
    handler = ShellHandler(events, timeout_s=0.05)
    handler.resolve(a_context(step=7))

    published = events.get_nowait()
    assert published["type"] == "interrupt"
    assert published["context"]["step"] == 7


def test_pending_is_visible_to_a_viewer_that_connects_mid_handover() -> None:
    handler = ShellHandler(queue.Queue(), timeout_s=5.0)
    threading.Thread(target=lambda: handler.resolve(a_context()), daemon=True).start()
    time.sleep(0.05)

    assert handler.pending is not None
    handler.decide(InterruptResolution(resolution=Resolution.APPROVED))


def test_deciding_with_nothing_pending_is_ignored_not_an_error() -> None:
    """A second click on Approve is a person being unsure, not a fault."""
    handler = ShellHandler(queue.Queue())
    assert handler.decide(InterruptResolution(resolution=Resolution.APPROVED)) is False


def test_pending_clears_after_a_decision() -> None:
    handler = ShellHandler(queue.Queue(), timeout_s=5.0)
    threading.Thread(target=lambda: handler.resolve(a_context()), daemon=True).start()
    time.sleep(0.05)

    handler.decide(InterruptResolution(resolution=Resolution.APPROVED))
    time.sleep(0.05)
    assert handler.pending is None


# -------------------------------------------------------------------------- session


class FakeScheduler:
    def __init__(self, steps: int = 3, fail: bool = False) -> None:
        self._steps = steps
        self._fail = fail

    def run_episode(self, policy, *, max_steps: int, seed=None):
        from tendon.kernel.scheduler import EpisodeResult

        if self._fail:
            raise RuntimeError("driver exploded")
        result = EpisodeResult(episode_id="ep-1")
        result.steps = min(self._steps, max_steps)
        return result


def a_session(**kwargs) -> EpisodeSession:
    return EpisodeSession(
        skill="test/skill",
        body_id="test:body",
        scheduler_factory=kwargs.pop("scheduler_factory", lambda h, s: FakeScheduler()),
        policy_factory=lambda: object(),
        **kwargs,
    )


def test_a_session_runs_and_finishes() -> None:
    session = a_session(max_steps=10)
    session.start()
    session.join(timeout=2.0)

    snapshot = session.snapshot()
    assert snapshot["finished"] is True
    assert snapshot["running"] is False
    assert snapshot["error"] is None


def test_a_worker_failure_is_surfaced_not_swallowed() -> None:
    """An episode that died silently is worse than one that failed loudly."""
    session = a_session(scheduler_factory=lambda h, s: FakeScheduler(fail=True))
    session.start()
    session.join(timeout=2.0)

    snapshot = session.snapshot()
    assert snapshot["finished"] is True
    assert snapshot["error"] is not None
    assert "driver exploded" in snapshot["error"]


def test_a_session_cannot_be_started_twice() -> None:
    session = a_session()
    session.start()
    with pytest.raises(RuntimeError):
        session.start()
    session.join(timeout=2.0)


def test_finishing_publishes_a_final_event() -> None:
    session = a_session()
    session.start()
    session.join(timeout=2.0)

    events = []
    while not session.events.empty():
        events.append(session.events.get_nowait())

    assert any(e["type"] == "finished" for e in events)


def test_a_full_event_queue_drops_rather_than_blocking() -> None:
    """A slow viewer must not slow the body down.

    Stale positions are worse than a gap: an operator watching a backlog is watching the
    past while the arm is somewhere else.
    """
    session = a_session()
    for i in range(1000):
        session.publish_intent(
            Observation(step=i, proprio=Proprioception(joint_positions=[0.0])),
            an_intent(),
        )

    # Never blocked, and the queue stayed bounded.
    assert session.events.qsize() <= 256


# -------------------------------------------------------------------------- registry


def test_only_one_episode_runs_at_a_time() -> None:
    """Two episodes on one body would fight over it, and interleaving them silently is
    worse than refusing."""
    from tendon.api.session import SessionRegistry

    registry = SessionRegistry()
    first = a_session()
    first.state.running = True
    registry.add(first)

    with pytest.raises(RuntimeError, match="still running"):
        registry.add(a_session())


def test_a_finished_session_does_not_block_the_next() -> None:
    from tendon.api.session import SessionRegistry

    registry = SessionRegistry()
    done = a_session()
    done.state.running = False
    registry.add(done)

    registry.add(a_session())
    assert len(registry.all()) == 2


# ------------------------------------------------- every message type has both ends


def test_deciding_publishes_resolved_for_other_viewers() -> None:
    """With one shell this is redundant; with two it is the difference between both seeing
    the decision and one still showing controls for a question already answered."""
    events: queue.Queue = queue.Queue()
    handler = ShellHandler(events, timeout_s=5.0)
    threading.Thread(target=lambda: handler.resolve(a_context(step=4)), daemon=True).start()
    time.sleep(0.05)

    handler.decide(InterruptResolution(resolution=Resolution.APPROVED))
    time.sleep(0.05)

    published = []
    while not events.empty():
        published.append(events.get_nowait())

    resolved = [e for e in published if e["type"] == "resolved"]
    assert resolved, "an operator decided and no viewer was told"
    assert resolved[0]["step"] == 4
    assert resolved[0]["resolution"] == "approved"


def test_every_message_the_runtime_sends_is_one_the_shell_handles() -> None:
    """The drift this test exists to catch was real and in both directions.

    The runtime sent `finished` and `error` that the shell ignored — an episode that had
    ended still looked like one that was running. The shell waited on `resolved`, which
    the runtime never sent. Nothing failed; the screen was simply wrong.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]

    sent = set()
    for name in ("app.py", "session.py"):
        source = (repo / "src" / "tendon" / "api" / name).read_text(encoding="utf-8")
        sent.update(re.findall(r'"type":\s*"([a-z]+)"', source))

    handled_source = (repo / "shell" / "src" / "state" / "session.ts").read_text(encoding="utf-8")
    handled = set(re.findall(r'case "([a-z]+)"', handled_source))

    unhandled = sent - handled
    assert not unhandled, (
        f"the runtime sends {sorted(unhandled)} and the shell ignores them; a message "
        "nobody handles is a screen that goes quietly stale"
    )
