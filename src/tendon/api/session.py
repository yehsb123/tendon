"""One running episode, watched from outside.

The scheduler is synchronous and blocking by design (ADR 0006, ADR 0005). The API is
asyncio. This is the bridge: the episode runs on a worker thread, publishes what it is
about to do, and blocks on a real threading primitive when it needs a human.

## The blocking is the point

`InterruptHandler.resolve` is called from inside the control loop and must not return until
someone decides. That is not a limitation to design around — it is what "hand over control"
means. A handler that returned immediately with a default would be approving on the
operator's behalf, which is the failure ADR 0003 names in a different form.

So `ShellHandler.resolve` waits on a `threading.Event`. If nobody answers within
`timeout_s`, it aborts the episode rather than proceeding: a body should not resume because
a person walked away.

## What crosses the boundary

    worker thread                          event loop
    ------------                           ----------
    scheduler.run_episode()   --events-->  websocket send
    ShellHandler.resolve()    <--decision- websocket receive

Both directions go through `queue.Queue`, which is thread-safe and does not require the
loop to be running when the worker writes to it. The reverse — an asyncio queue read from a
thread — is not safe, and getting that wrong produces a hang rather than an error.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tendon.kernel.interrupt import InterruptState
from tendon.kernel.scheduler import EpisodeResult, Scheduler, StepRecord
from tendon.kernel.types import (
    InterruptContext,
    InterruptResolution,
    Observation,
    Resolution,
)

__all__ = ["EpisodeSession", "SessionState", "ShellHandler"]

#: How long an interrupt waits for a human before giving up [s]. Aborting is the safe
#: answer: a body must not resume because nobody was watching.
_DEFAULT_TIMEOUT_S = 300.0

#: Live state is dropped rather than queued without bound. A slow viewer must not make the
#: control loop wait, and stale state is worse than missing state.
_EVENT_QUEUE_SIZE = 256


@dataclass
class SessionState:
    session_id: str
    skill: str
    body_id: str
    running: bool = False
    finished: bool = False
    steps: int = 0
    interventions: int = 0
    corrections: int = 0
    #: Set when the episode ended.
    result: EpisodeResult | None = None
    #: Set when the worker raised. An episode that died silently is worse than one that
    #: failed loudly, so this is surfaced rather than logged.
    error: str | None = None


class ShellHandler:
    """An `InterruptHandler` that asks whoever is connected.

    Blocks the control loop until a decision arrives or the timeout expires. That is the
    correct behaviour: the deliberation tier is supposed to stop while a human decides, and
    the control tier holds position underneath.
    """

    def __init__(
        self,
        events: queue.Queue,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        on_resolved: Callable[[InterruptContext, InterruptResolution], None] | None = None,
    ) -> None:
        """
        Args:
            on_resolved: Called with the context and the decision once an interrupt is
                answered, including when it timed out. This is where the recorder writes
                the interrupt into the episode. It gets the `InterruptContext`, which is
                what `Recorder.note_interrupt` needs and what the scheduler's
                `on_intervention` hook does not carry — that one hands over the
                observation, because it exists for a policy to learn from rather than for
                a store to describe.
        """
        self._events = events
        self._timeout_s = timeout_s
        self._on_resolved = on_resolved
        self._pending: InterruptContext | None = None
        self._decision: InterruptResolution | None = None
        self._answered = threading.Event()
        self._lock = threading.Lock()

    @property
    def pending(self) -> InterruptContext | None:
        """The interrupt awaiting a decision, for a viewer that connected mid-handover."""
        with self._lock:
            return self._pending

    def resolve(self, context: InterruptContext) -> InterruptResolution:
        with self._lock:
            self._pending = context
            self._decision = None
        self._answered.clear()

        _offer(self._events, {"type": "interrupt", "context": context.model_dump(mode="json")})

        if not self._answered.wait(timeout=self._timeout_s):
            with self._lock:
                self._pending = None
            # Aborting rather than approving. Nobody answered, so nobody approved.
            timed_out = InterruptResolution(
                resolution=Resolution.ABORTED,
                note=f"no operator decision within {self._timeout_s:g}s",
            )
            # Recorded like any other outcome. An episode that stopped because nobody was
            # watching is a fact about the run, and leaving it out of the store would make
            # the abandoned episodes the invisible ones.
            self._notify(context, timed_out)
            return timed_out

        with self._lock:
            decision = self._decision
            self._pending = None

        resolution = decision or InterruptResolution(resolution=Resolution.ABORTED)
        self._notify(context, resolution)
        return resolution

    def _notify(self, context: InterruptContext, resolution: InterruptResolution) -> None:
        """Tell whoever is listening what was decided, without letting them stop the body.

        Suppressed for the same reason the scheduler suppresses `on_intervention`: a
        recorder that cannot write is not a reason to strand a robot mid-motion. The
        failure surfaces through the bus, which is where a dropped subscriber is reported.
        """
        if self._on_resolved is None:
            return
        with contextlib.suppress(Exception):
            self._on_resolved(context, resolution)

    def decide(self, resolution: InterruptResolution) -> bool:
        """Answer the pending interrupt. Returns False when there is nothing to answer.

        Returning False rather than raising: a second click on Approve is a person being
        unsure, not an error, and the shell should be able to ignore it quietly.
        """
        with self._lock:
            if self._pending is None:
                return False
            step = self._pending.step
            self._decision = resolution
        self._answered.set()

        # Published so every viewer stays in sync. With one shell this is redundant; with
        # two it is the difference between both seeing the decision and one of them still
        # showing controls for a question that has been answered.
        _offer(
            self._events,
            {
                "type": "resolved",
                "step": step,
                "resolution": resolution.resolution.value,
            },
        )
        return True


class EpisodeSession:
    """Runs one episode on a worker thread and publishes what it does.

    Deliberately one episode rather than a queue of them. A session that outlived an
    episode would need to answer what happens to a pending interrupt when the next one
    starts, and the honest answer is that it should not be possible.
    """

    def __init__(
        self,
        *,
        skill: str,
        body_id: str,
        scheduler_factory,
        policy_factory,
        max_steps: int = 500,
        seed: int | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        before_episode: Callable[[], None] | None = None,
        after_episode: Callable[[], None] | None = None,
        on_resolved: Callable[[InterruptContext, InterruptResolution], None] | None = None,
    ) -> None:
        """
        Args:
            before_episode: Called on the episode thread just before the body moves, and
                `after_episode` in a `finally` once it stops. They exist so a recorder can
                be opened and closed around the run without this module knowing what a
                recorder is — the episode happens on a thread nobody else can reach, so
                there is no other moment for the caller to take.
        """
        self.state = SessionState(session_id=uuid.uuid4().hex, skill=skill, body_id=body_id)
        self.events: queue.Queue = queue.Queue(maxsize=_EVENT_QUEUE_SIZE)
        self.handler = ShellHandler(self.events, timeout_s=timeout_s, on_resolved=on_resolved)

        self._scheduler_factory = scheduler_factory
        self._policy_factory = policy_factory
        self._max_steps = max_steps
        self._seed = seed
        self._before_episode = before_episode
        self._after_episode = after_episode
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("session already started")
        self.state.running = True
        self._thread = threading.Thread(target=self._run, name="tendon-episode", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            policy = self._policy_factory()
            scheduler: Scheduler = self._scheduler_factory(self.handler, self._on_step)

            if self._before_episode is not None:
                self._before_episode()
            try:
                result = scheduler.run_episode(policy, max_steps=self._max_steps, seed=self._seed)
            finally:
                # In `finally` so a run that raises still closes whatever was opened. A
                # dataset left half written is what the store reports as unreadable later.
                if self._after_episode is not None:
                    self._after_episode()

            self.state.result = result
            self.state.steps = result.steps
            self.state.interventions = result.interventions
            self.state.corrections = result.corrections
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            self.state.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.state.running = False
            self.state.finished = True
            _offer(self.events, {"type": "finished", "state": self.snapshot()})

    def _on_step(self, record: StepRecord) -> None:
        """Publish a step. Never blocks the control loop.

        Dropped when the queue is full rather than queued without bound: a slow viewer
        must not slow the body down, and a backlog of stale positions is worse than a gap.
        """
        self.state.steps = record.step + 1
        _offer(
            self.events,
            {
                "type": "state",
                "step": record.step,
                "observation": record.observation.model_dump(mode="json"),
                "commanded": record.commanded.model_dump(mode="json"),
                "applied": record.applied.model_dump(mode="json"),
                "clamped": record.clamped,
            },
        )

    def publish_intent(self, observation: Observation, intent: Any) -> None:
        """Announce what is about to run, before it runs."""
        _offer(
            self.events,
            {
                "type": "intent",
                "intent": intent.model_dump(mode="json"),
                "step": observation.step,
            },
        )

    def snapshot(self) -> dict[str, Any]:
        state = self.state
        return {
            "session_id": state.session_id,
            "skill": state.skill,
            "body_id": state.body_id,
            "running": state.running,
            "finished": state.finished,
            "steps": state.steps,
            "interventions": state.interventions,
            "corrections": state.corrections,
            "error": state.error,
            "ended": (
                state.result.state.value
                if state.result is not None
                else InterruptState.RUNNING.value
            ),
        }

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)


@dataclass
class SessionRegistry:
    """The sessions this runtime knows about.

    One at a time for now. Two episodes on one body would fight over it, and the honest
    way to say that is to refuse rather than to interleave.
    """

    _sessions: dict[str, EpisodeSession] = field(default_factory=dict)

    def add(self, session: EpisodeSession) -> None:
        live = [s for s in self._sessions.values() if s.state.running]
        if live:
            raise RuntimeError(
                f"session {live[0].state.session_id} is still running on {live[0].state.body_id}"
            )
        self._sessions[session.state.session_id] = session

    def get(self, session_id: str) -> EpisodeSession | None:
        return self._sessions.get(session_id)

    def all(self) -> tuple[EpisodeSession, ...]:
        return tuple(self._sessions.values())


def _offer(q: queue.Queue, item: dict[str, Any]) -> None:
    """Put without blocking. Drops the oldest item when full.

    The control loop calls this. It must never wait on a consumer.
    """
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
            q.put_nowait(item)
        except (queue.Empty, queue.Full):
            pass
