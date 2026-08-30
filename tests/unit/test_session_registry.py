"""The registry is a window, not an archive.

Finished sessions stayed for the life of the process. Each one holds an `EpisodeResult`
and, through it, every step's observation and both actions — about 728 bytes a step, so
364 KB for a 500-step episode. A runtime left up for a day of teaching accumulated one per
episode, for data nothing in the API ever reads again.

This is the same shape as the body that was never closed: something acquired per session
and released never. It does not fail; it grows. The failure it eventually produces is a
memory figure somebody notices long after the sessions that caused it, which is the worst
kind to diagnose.

## Why dropping history is the right answer

The durable record is elsewhere and better. Episodes are in the store, the interrupt rows
are in each sidecar, and the progress log has one line per episode with both axes of the
graph. The registry only has to answer "what is happening in the session I am watching",
and a little history around it.
"""

from __future__ import annotations

import pytest

from tendon.api.session import EpisodeSession, SessionRegistry


def a_session(*, running: bool = False) -> EpisodeSession:
    """A session that has never been started, with its state set by hand.

    Not started, because starting one spawns a thread and drives a scheduler. What is under
    test is the bookkeeping.
    """
    session = EpisodeSession(
        skill="grasp/cube-sim",
        body_id="mujoco",
        scheduler_factory=lambda handler, on_step: None,
        policy_factory=lambda: None,
    )
    session.state.running = running
    return session


# ------------------------------------------------------------------ one at a time


def test_a_second_session_is_refused_while_one_runs() -> None:
    """Two episodes on one body would fight over it, and refusing says so."""
    registry = SessionRegistry()
    registry.add(a_session(running=True))

    with pytest.raises(RuntimeError, match="still running"):
        registry.add(a_session())


def test_a_finished_session_does_not_block_the_next() -> None:
    registry = SessionRegistry()
    registry.add(a_session())
    registry.add(a_session())

    assert len(registry.all()) == 2


# ------------------------------------------------------------------- and bounded


def test_old_sessions_are_dropped() -> None:
    registry = SessionRegistry(limit=3)
    for _ in range(10):
        registry.add(a_session())

    assert len(registry.all()) == 3


def test_the_ones_kept_are_the_recent_ones() -> None:
    """Oldest first, because the session somebody is asking about is the one that just
    ran."""
    registry = SessionRegistry(limit=2)
    sessions = [a_session() for _ in range(4)]
    for session in sessions:
        registry.add(session)

    kept = {s.state.session_id for s in registry.all()}
    assert kept == {sessions[-2].state.session_id, sessions[-1].state.session_id}


def test_a_running_session_is_never_dropped() -> None:
    """It is the one being watched. Evicting it would make `GET /api/sessions/{id}` return
    404 for an episode that is currently moving a robot."""
    registry = SessionRegistry(limit=2)
    running = a_session(running=True)
    registry.add(running)

    # `add` refuses while one runs, so fill the registry the way time would: by hand.
    for _ in range(5):
        registry._sessions[a_session().state.session_id] = a_session()
    registry._evict()

    assert registry.get(running.state.session_id) is not None


def test_a_dropped_session_is_simply_not_found() -> None:
    """`get` already returns None for an unknown id, and the shell already handles that —
    an evicted session is indistinguishable from one this runtime never had, which is
    exactly what it now is."""
    registry = SessionRegistry(limit=1)
    first = a_session()
    registry.add(first)
    registry.add(a_session())

    assert registry.get(first.state.session_id) is None


def test_the_limit_is_small_on_purpose() -> None:
    """Stated as a test because the tempting change is to raise it until the problem goes
    away, and the problem is unbounded growth rather than the number twenty."""
    assert SessionRegistry().limit <= 50
