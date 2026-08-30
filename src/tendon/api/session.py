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
import time
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

#: How often a pending decision checks whether anybody is still there [s]. Short enough
#: that an abandoned handover ends promptly, long enough that this is a wait rather than a
#: spin — the thread is holding a body in position while it runs.
_DECISION_POLL_S = 0.1

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
    #: Where this policy's uncertainty comes from. `"stand-in"` when it is placed in joint
    #: space on purpose so the loop has something to hand over about — which is what runs
    #: today, and which an operator watching the policy "raise its own hand" has no other
    #: way to know. ADR 0003 says confidence has no upstream source yet; this is the
    #: sentence that carries that decision to the person in front of it.
    uncertainty: str = "stand-in"
    #: True when this episode is being written to the store. False when LeRobot is missing,
    #: which the CLI says out loud and the shell had no way to know — an operator would
    #: correct a policy for an afternoon and find `Episodes` empty.
    recording: bool = True
    #: Why the episode ended early, if something outside it decided. Set from either of the
    #: two places that can stop one: the scheduler declining to ask for another chunk, and
    #: a pending decision being given up on. Both are the same condition and they surface
    #: differently, so an operator reading a short run would otherwise see a reason in one
    #: case and nothing in the other.
    stopped_because: str | None = None


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
        give_up_when: Callable[[], str | None] | None = None,
    ) -> None:
        """
        Args:
            give_up_when: Checked while waiting for a decision. Returning a reason ends the
                wait and aborts, rather than holding the body for the full timeout with
                nobody able to answer.
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
        self._give_up_when = give_up_when
        self._gave_up: str | None = None
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

        if not self._wait_for_a_decision():
            with self._lock:
                self._pending = None
            # Aborting rather than approving. Nobody answered, so nobody approved.
            timed_out = InterruptResolution(
                resolution=Resolution.ABORTED,
                note=self._gave_up or f"no operator decision within {self._timeout_s:g}s",
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

    def _wait_for_a_decision(self) -> bool:
        """Block until an operator answers, or until waiting stops making sense.

        The plain `Event.wait(timeout)` this replaced was the more dangerous half of the
        disconnect gap. Stopping between chunks does nothing while the scheduler is
        *inside* a handover — and a handover with nobody connected is exactly the case
        worth stopping: the body is held, the question has been asked, and the only thing
        that could answer it has gone.

        So the wait is taken in slices and the abandonment check runs between them. The
        slice is short enough that an operator does not notice and long enough that this
        is not a spin.
        """
        self._gave_up = None
        if self._give_up_when is None:
            return self._answered.wait(timeout=self._timeout_s)

        deadline = time.monotonic() + self._timeout_s
        while time.monotonic() < deadline:
            if self._answered.wait(timeout=_DECISION_POLL_S):
                return True
            reason = self._give_up_when()
            if reason is not None:
                self._gave_up = f"{reason} while a decision was pending"
                return False
        return False

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
        on_result: Callable[[EpisodeResult], None] | None = None,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        """
        Args:
            before_episode: Called on the episode thread just before the body moves, and
                `after_episode` in a `finally` once it stops. They exist so a recorder can
                be opened and closed around the run without this module knowing what a
                recorder is — the episode happens on a thread nobody else can reach, so
                there is no other moment for the caller to take.
            on_closed: Called last, whatever happened, for releasing what the caller opened
                before handing it over. The body is the reason: `create_app` opens one per
                session and closed it only on the failure paths, so every episode that
                *worked* leaked a MuJoCo model — and would have left a physical arm's serial
                port open, which the next session then cannot acquire.

                Separate from `after_episode` because that one sits inside the episode. A
                policy factory that raises means there was no episode, and the body still
                needs closing.
        """
        self.state = SessionState(session_id=uuid.uuid4().hex, skill=skill, body_id=body_id)
        self.events: queue.Queue = queue.Queue(maxsize=_EVENT_QUEUE_SIZE)
        self.handler = ShellHandler(
            self.events,
            timeout_s=timeout_s,
            on_resolved=on_resolved,
            # The handler asks the session, so the same condition ends a wait and stops the
            # next chunk. Two answers to "is anybody there" would eventually disagree.
            give_up_when=self._give_up_on_a_decision,
        )

        self._scheduler_factory = scheduler_factory
        self._policy_factory = policy_factory
        self._max_steps = max_steps
        self._seed = seed
        #: Sockets currently attached, and whether one ever was. Both are needed: an
        #: episode that nobody has watched yet is ordinary — the shell posts and then
        #: connects — while an episode that had a viewer and now has none has lost the only
        #: thing that can answer an interrupt.
        self._viewers = 0
        self._ever_watched = False

        self._before_episode = before_episode
        self._after_episode = after_episode
        self._on_result = on_result
        self._on_closed = on_closed
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
            if result.stopped_because is not None and self.state.stopped_because is None:
                # The scheduler declined to ask for another chunk. Not overwritten if a
                # pending decision was already given up on: that one happened first and
                # says more about what the operator missed.
                self.state.stopped_because = result.stopped_because

            if self._on_result is not None:
                # After the episode succeeded, not in the `finally` above: this is for
                # recording what an episode *was*, and an episode that raised does not
                # have one. `after_episode` is the one that must run either way, because
                # it closes things.
                with contextlib.suppress(Exception):
                    self._on_result(result)

            # Everything that wanted the steps has had them: the recorder took each one
            # off the bus as it happened, and `on_result` has just written the episode's
            # line to the progress log. Nothing in the API reads them again, and holding
            # them is what made a long-running server grow by 364 KB an episode.
            #
            # Cleared rather than never collected: the scheduler returns them because
            # `tendon run` prints from them, and a kernel that guessed which caller cared
            # would be guessing.
            result.records.clear()
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            self.state.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.state.running = False
            self.state.finished = True

            if self._on_closed is not None:
                # The outermost `finally`, which is the only place that runs whatever
                # happened — including a policy factory that raised before an episode
                # existed at all. `after_episode` sits inside the episode and would be
                # skipped by exactly that case, which is how a body stays open.
                with contextlib.suppress(Exception):
                    self._on_closed()

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

    def watching(self) -> None:
        """A socket attached."""
        self._viewers += 1
        self._ever_watched = True

    def stopped_watching(self) -> None:
        """A socket detached. Never negative: a handler that unwinds twice must not make
        the count say somebody is still there."""
        self._viewers = max(0, self._viewers - 1)

    def abandoned(self) -> str | None:
        """Why the deliberation tier should stop, or None to keep going.

        `SECURITY.md` states the intended property: a connection loss must not leave a body
        mid-motion. It used to claim the deliberation tier stopped, which was not
        implemented — an episode ran on unattended to its step limit with nobody able to
        answer if it asked for help.

        Only after somebody has actually watched. An episode nobody ever connected to is
        the ordinary case for `tendon run` and for a test, and stopping those would be
        stopping the thing this is meant to protect.
        """
        if self._ever_watched and self._viewers == 0:
            return "the last operator disconnected"
        return None

    def _give_up_on_a_decision(self) -> str | None:
        """The same condition the scheduler asks, recorded when it actually fires.

        Two answers to "is anybody there" would eventually disagree, so both paths call
        `abandoned`. Only this one can record why, because the abort it produces is a
        normal-looking ending: without this a reader sees a short episode, an aborted
        state, and no reason anywhere on the session.
        """
        reason = self.abandoned()
        if reason is not None and self.state.stopped_because is None:
            self.state.stopped_because = f"{reason} while a decision was pending"
        return reason

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
            "uncertainty": state.uncertainty,
            "recording": state.recording,
            # Why it ended early, when something outside the episode asked. An operator
            # who reconnects to a finished run should be told it stopped because they were
            # gone, rather than left to read a short step count as a completed episode.
            "stopped_because": state.stopped_because,
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

    **A window, not an archive.** Finished sessions used to stay for the life of the
    process, so a runtime left up all day accumulated one per episode — each holding an
    `EpisodeResult` and, through it, every step's observation and both actions. At roughly
    728 bytes a step and 500 steps an episode that is 364 KB per run kept forever, for
    data nothing in the API ever reads again.

    The durable record is the episode store and the progress log. This only has to answer
    "what happened in the session I am watching", and a little history around it.
    """

    #: How many sessions to remember. Small on purpose: the shell follows one at a time,
    #: and anything older is a question for `tendon episodes` or `tendon progress`.
    limit: int = 20

    _sessions: dict[str, EpisodeSession] = field(default_factory=dict)

    def add(self, session: EpisodeSession) -> None:
        live = [s for s in self._sessions.values() if s.state.running]
        if live:
            raise RuntimeError(
                f"session {live[0].state.session_id} is still running on {live[0].state.body_id}"
            )
        self._sessions[session.state.session_id] = session
        self._evict()

    def _evict(self) -> None:
        """Drop the oldest finished sessions past the limit.

        Insertion order, which for a dict is the order they were added and therefore the
        order they ran. A running session is never dropped: it is the one somebody is
        watching, and `add` has already refused to start a second.
        """
        while len(self._sessions) > self.limit:
            for session_id, session in self._sessions.items():
                if not session.state.running:
                    del self._sessions[session_id]
                    break
            else:  # pragma: no cover - `add` refuses a second running session
                return

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
