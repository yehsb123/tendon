"""The surface the shell talks to.

Two channels, deliberately separated:

    REST       skills, bodies, sessions, decisions
    WebSocket  live intent, state, interrupt raise and resolve

The intent stream is latency-critical: an operator has to see what the robot is about to
do while it is still about to do it. Anything precomputable belongs to REST so the socket
carries only what must be live.

## This module is a boundary

A handler that does more than translate between HTTP and a kernel or service call has
logic in the wrong layer. Nothing here decides anything — it reads, it serialises, it
hands off.

## What it deliberately does not do yet

Authentication. `SECURITY.md` says the shell assumes a trusted network and that this is
v0.4 work, and listing it there rather than leaving it implied is the point: a reader
should not have to discover the absence by looking for the code.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue as queue_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tendon import __version__

__all__ = ["create_app"]

#: For failures that must not stop an episode and must not disappear either. A robot
#: mid-motion is not a reason to raise, and a write that silently never happened is not
#: something anybody discovers until they need the thing that was not written.
_LOG = logging.getLogger("tendon.api")

#: Where skills are looked for when no explicit root is configured.
_DEFAULT_SKILL_ROOT = Path("skills")

#: How often the socket checks the worker queue when it is empty [s].
_POLL_INTERVAL_S = 0.02

#: Built shell assets, served when they exist so one command is enough.
#:
#: Relative, and therefore resolved against the working directory. That is deliberate for a
#: repository checkout and it means `tendon serve` from anywhere else finds nothing —
#: `shell_root()` is what callers use to say so rather than leave a blank page.
_SHELL_DIST = Path("shell") / "dist"


def shell_root() -> Path | None:
    """The built shell this process would serve, or None if there is none to serve.

    Exported so `tendon serve` can report it. The mount used to happen silently: somebody
    running the command outside a checkout got a working API, a blank page, and nothing
    connecting the two.
    """
    return _SHELL_DIST.resolve() if _SHELL_DIST.is_dir() else None


class StartRequest(BaseModel):
    """What to run. Kept small on purpose — a session is one episode."""

    skill: str
    body: str = "mujoco"
    max_steps: int = 500
    seed: int | None = None
    #: Seconds an interrupt waits for a decision before aborting the episode.
    timeout_s: float = 300.0
    #: Required to run on a body that moves real hardware. Defaults to false so that
    #: reaching a physical arm is never something a request does by omission.
    allow_physical: bool = False


class DecisionRequest(BaseModel):
    """An operator answering a pending interrupt.

    `correction` is a full `Intent`. A correction expressed as a delta would need the
    runtime to reconstruct what it was relative to, and a reconstruction that is even
    slightly wrong is a motion nobody chose.
    """

    resolution: str
    correction: dict[str, Any] | None = None
    note: str | None = None


def _open_recorder(loaded, root: Path):
    """A recorder for one session, or None when LeRobot is not installed.

    Returns rather than raises when it is missing: the kernel and the simulator both work
    without it, and refusing to start an episode over an optional extra would make the
    shell unusable on a machine that can still drive a body. The session reports what it
    is doing either way.
    """
    try:
        from tendon.services.recorder import Recorder
    except ImportError:
        return None

    # Under the skill's own reference, matching what `tendon run` writes, so an episode
    # started from the shell and one started from the command line land together.
    return Recorder(root=root, repo_id=loaded.ref)


def _learn_and_keep(policy, memory_root, skill, body_id, observation, resolution) -> None:
    """Teach the policy, then write down what it learned.

    Saving belongs here rather than beside `note_interrupt`, and getting that wrong is
    instructive: the handler fires when the *decision arrives*, and the scheduler teaches
    the policy afterwards. Saving from the handler wrote an empty memory every time — the
    file appeared, was valid, and held nothing, which is the most convincing kind of wrong.

    Written on each correction rather than at the end of the episode. A correction is a
    thing a person did, and an episode that fails afterwards should not take it with it.
    Corrections arrive at human speed, nowhere near the control loop.
    """
    from tendon.services.memory_store import save_memory

    if not policy.learn_from(observation, resolution):
        # Nothing was learned — an approval, or a rejection with no replacement. Writing
        # the file anyway would rewrite it on every interrupt for no change.
        return

    try:
        save_memory(memory_root, skill, body_id, policy.memory)
    except Exception as exc:  # noqa: BLE001 - isolation, not silence
        # Not raised: this runs on the episode thread and a robot mid-motion is not a
        # reason to throw. Not suppressed either. The first version of this swallowed
        # everything and never wrote a byte — the body id contains a colon, illegal in a
        # Windows filename — and nothing in the running system would have said so.
        _LOG.warning(
            "could not save what was taught for %s on %s: %s; it is still held for this "
            "run and will be lost on restart",
            skill,
            body_id,
            exc,
        )


def _effective(loaded):
    """The limits that will actually be enforced for this skill on this machine.

    One function because two routes need it and they must not disagree: the session route
    decides what the scheduler enforces, and the detail route tells an operator what that
    is. A view answering "what is this motion not allowed to do" with the number the file
    asked for rather than the number in force is the more dangerous of the two to get wrong.

    Raises `LocalLimitsError` for a ceiling that exists and cannot be read. Both callers
    refuse rather than fall back: a site that wrote one believes it has a bound.
    """
    from tendon.services.limits import load_local_limits, tighten

    return tighten(loaded.limits, load_local_limits())


def _record_progress(progress_root, skill, body_id, memories, result) -> None:
    """Append what this episode cost in human attention.

    Written after the episode rather than during it, and isolated: a log that cannot be
    appended to must not turn a finished run into a failed one. Reported for the same
    reason as the memory — a line that silently never appeared is a graph with a hole in
    it that nobody can see.
    """
    from tendon.services import progress

    memory = memories.get((skill, body_id))
    try:
        progress.append(
            progress_root,
            skill,
            body_id,
            progress.EpisodeRecord(
                skill=skill,
                body=body_id,
                episode_id=result.episode_id,
                ended_at=progress.now(),
                steps=result.steps,
                interventions=result.interventions,
                corrections=result.corrections,
                corrections_known=len(memory) if memory is not None else 0,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - isolation, not silence
        _LOG.warning("could not record progress for %s on %s: %s", skill, body_id, exc)


def _record_decision(recorder, context, resolution, skill) -> None:
    """Write an operator's decision into the episode.

    Isolated because a recorder that cannot write must not cost the operator the decision
    they just made — and reported, because a write that silently never happened is not
    something anybody discovers until they need what was not written.
    """
    if recorder is None:
        return
    try:
        recorder.note_interrupt(context, resolution)
    except Exception as exc:  # noqa: BLE001 - isolation, not silence
        _LOG.warning("could not record the interrupt for %s: %s", skill, exc)


def create_app(
    *,
    skill_root: Path | None = None,
    episode_root: Path | None = None,
    memory_root: Path | None = None,
    progress_root: Path | None = None,
) -> FastAPI:
    """Build the API.

    `skill_root` and `episode_root` are injected rather than read from globals so tests
    can point them at fixture directories, and so a deployment can serve skills from
    somewhere other than the working directory and write episodes somewhere other than
    the home directory. A test that used the real store would put its episodes in the
    operator's data.
    """
    from tendon.api.session import SessionRegistry
    from tendon.services.memory_store import DEFAULT_MEMORY_ROOT
    from tendon.services.progress import DEFAULT_PROGRESS_ROOT
    from tendon.services.store import DEFAULT_ROOT

    root = skill_root if skill_root is not None else _DEFAULT_SKILL_ROOT
    episode_root = episode_root if episode_root is not None else DEFAULT_ROOT
    memory_root = memory_root if memory_root is not None else DEFAULT_MEMORY_ROOT
    progress_root = progress_root if progress_root is not None else DEFAULT_PROGRESS_ROOT

    # What the operator has taught, kept across sessions rather than per episode.
    #
    # `examples/04_improve` says why in its own comment: one memory across every episode,
    # because what somebody taught in episode 3 has to still be there in episode 30. The
    # shell built a fresh `AdaptivePolicy` for each session, so a correction survived
    # exactly as long as the episode it was made in — and the intervention rate could
    # never fall no matter how patient the operator was. The claim this interface exists
    # to demonstrate could not be demonstrated through it.
    #
    # Keyed on skill *and* body: a correction is a joint-space position, so it means
    # nothing on a body with different kinematics, and nothing about a different task.
    #
    # Loaded from disk on first use and written on every correction, so an afternoon of
    # teaching survives a restart. `services/memory_store.py` says why that file is
    # separate from the episode sidecar: an episode is history and never changes, a memory
    # is current knowledge and changes whenever somebody corrects something.
    memories: dict[tuple[str, str], Any] = {}
    registry = SessionRegistry()

    app = FastAPI(
        title="tendon",
        version=__version__,
        summary="The operating layer for physical AI",
    )

    # ------------------------------------------------------------------- discovery

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """Liveness, and enough to tell which runtime the shell is talking to.

        The shell shows the version because a shell built against a different contract is
        the failure that looks like a bug everywhere else.
        """
        return {"status": "ok", "version": __version__}

    @app.get("/api/bodies")
    async def bodies() -> list[dict[str, Any]]:
        """Bodies this runtime can load, and why any of them cannot be."""
        from tendon.services.bodies import discover

        return [
            {
                "name": info.name,
                "available": info.available,
                "detail": info.unavailable_because,
                # The shell shows this prominently. Someone approving a motion needs to
                # know whether it happens in a window or in the room.
                "simulated": info.simulated,
            }
            for info in discover()
        ]

    @app.get("/api/skills")
    async def skills() -> list[dict[str, Any]]:
        """Skills found under the skill root.

        A skill that fails to load is listed with its error rather than omitted. Silently
        dropping it would leave someone staring at a directory that exists and a list that
        does not mention it.
        """
        from tendon.services.limits import LocalLimitsError
        from tendon.services.skill import SkillError, load_skill

        found: list[dict[str, Any]] = []
        for path in sorted(root.glob("*/*/skill.yaml")):
            try:
                loaded = load_skill(path)
            except SkillError as exc:
                found.append({"ref": str(path.parent), "error": str(exc)})
                continue

            try:
                # Once per skill, not once per field: reading the ceiling twice would let a
                # file changing between the two reads produce a row that disagrees with
                # itself about whether it was capped.
                effective = _effective(loaded)
            except LocalLimitsError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            found.append(
                {
                    "namespace": loaded.namespace,
                    "name": loaded.name,
                    "ref": loaded.ref,
                    "version": loaded.version,
                    "summary": loaded.summary,
                    "confidence_threshold": loaded.confidence_threshold,
                    "requires": {
                        "dof": loaded.requires.dof,
                        "gripper": (
                            loaded.requires.gripper.value if loaded.requires.gripper else None
                        ),
                        "cameras": list(loaded.requires.cameras),
                        "control_hz": loaded.requires.control_hz,
                    },
                    # The same correction as the detail route, one route over. Fixing that
                    # one and not this one is how a bug survives being found: the list is
                    # where somebody looks first, and it was answering with the skill's own
                    # numbers while the scheduler enforced something tighter.
                    "safety": effective.model_dump(),
                    "policy_base": loaded.policy_base,
                }
            )
        return found

    @app.get("/api/skills/{namespace}/{name}")
    async def skill_detail(namespace: str, name: str) -> dict[str, Any]:
        from tendon.services.limits import LocalLimitsError
        from tendon.services.skill import SkillError, load_skill

        try:
            loaded = load_skill(root / namespace / name / "skill.yaml")
        except SkillError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            effective = _effective(loaded)
        except LocalLimitsError as exc:
            # Not falling back to the declared limits. This view exists to say what a
            # motion is not allowed to do, and answering with the skill's own numbers while
            # a broken ceiling sits on disk would be answering confidently and wrongly.
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return {
            "ref": loaded.ref,
            "version": loaded.version,
            "summary": loaded.summary,
            "license": loaded.license,
            "confidence_threshold": loaded.confidence_threshold,
            # What will actually be enforced, not what the file asked for. The two differ
            # whenever this machine has a ceiling, and a view whose whole purpose is
            # "what is this motion not allowed to do" must not answer with the looser
            # number. `declared` is kept so an operator can see that something narrowed it.
            "safety": effective.model_dump(),
            "declared": loaded.limits.model_dump(),
            "capped": effective != loaded.limits,
            "success_criteria": [
                {"condition": condition, "threshold": threshold}
                for condition, threshold in loaded.success_criteria
            ],
            "eval_episodes": loaded.eval_episodes,
        }

    @app.get("/api/skills/{namespace}/{name}/compatibility/{body}")
    async def compatibility(namespace: str, name: str, body: str) -> dict[str, Any]:
        """Whether this skill can run on that body, and every reason it cannot.

        Exposed so the shell can grey out a body with the reasons attached, instead of
        letting an operator start a run that fails at load.
        """
        from tendon.services.bodies import BodyUnavailable, open_body
        from tendon.services.skill import SkillError, check_compatibility, load_skill

        try:
            loaded = load_skill(root / namespace / name / "skill.yaml")
        except SkillError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            driver = open_body(body)
        except BodyUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            reasons = check_compatibility(loaded, driver)
        finally:
            driver.close()

        return {"compatible": not reasons, "reasons": list(reasons)}

    @app.get("/api/skills/{namespace}/{name}/curation")
    async def curation(namespace: str, name: str, limit: int = 0) -> dict[str, Any]:
        """Recorded episodes for one skill, ranked by what is worth training on.

        `curator.ScoredEpisode.reasons` says of itself that it is "shown in the shell,
        because a bare number gives a reviewer nothing to disagree with". There was no
        shell view. The scores existed, the reasons existed, and the only way to read
        either was a command.

        Never deletes and never filters by threshold — the ordering is the output and the
        removal is a person's decision, which is exactly why this is a view rather than a
        job.
        """
        from tendon.services.episodes import EpisodeReadError, rank_episodes

        try:
            ranking = rank_episodes(episode_root / f"{namespace}__{name}", limit=limit or None)
        except EpisodeReadError:
            # Not a 404. The skill exists and simply has no episodes yet, which is the
            # normal state before anybody has run it, and an error would make the view
            # shout about something ordinary.
            return {"episodes": [], "interrupts_known": True}

        return {
            "episodes": [
                {
                    "episode_id": entry.episode_id,
                    "score": entry.score,
                    "steps": entry.signals.steps,
                    "had_interrupt": entry.signals.had_interrupt,
                    "reasons": list(entry.reasons),
                }
                for entry in ranking.scored
            ],
            "interrupts_known": ranking.interrupts_known,
        }

    @app.get("/api/progress")
    async def progress_view(window: int = 10) -> list[dict[str, Any]]:
        """Is it asking less often than it used to, per skill and body.

        The graph `docs/roadmap.md` measures v0.3 by: cumulative corrections against a
        trailing intervention rate. It had been produced twice, by a script and by a test,
        and never by the running system — so an operator correcting a policy for a week
        could not see whether any of it was working.

        `points` is empty until a full window of episodes exists. A rate over three
        episodes is not a rate, and drawing one invites reading a trend off noise.
        """
        from tendon.services import progress as progress_module

        return [
            {
                "skill": skill,
                "body": body,
                "episodes": len(records),
                "corrections": records[-1].corrections_known,
                "window": window,
                "points": [
                    {"corrections": x, "rate": y}
                    for x, y in progress_module.rate_curve(records, window=window)
                ],
            }
            for skill, body, records in progress_module.logs(progress_root)
        ]

    @app.get("/api/memory")
    async def memory() -> list[dict[str, Any]]:
        """What the operator has taught, per skill and body.

        The shell can show that the policy asks less often. It could not show *why*, and
        from the operator's seat "it learned" and "it got lucky" looked identical. This is
        the difference: a count of corrections held, and where in joint space they were
        given.

        Reported from the live memory rather than from the store, because the store does
        not have it — `note_interrupt` records that a correction happened, not what it
        was. That gap is why this does not survive a restart yet (docs/collaboration.md).
        """
        return [
            {
                "skill": skill,
                "body": body_id,
                "corrections": len(entry),
                # Where each was taught. The joint positions are what `recall` measures
                # distance against, so this is the actual index, not a summary of it.
                "taught_at": [list(positions) for positions, _ in entry.entries],
                "radius": entry.radius,
            }
            for (skill, body_id), entry in sorted(memories.items())
        ]

    @app.get("/api/episodes")
    async def episodes() -> list[dict[str, Any]]:
        """What has been recorded.

        Reads the layout on disk rather than opening datasets through LeRobot, so it
        answers on a machine that cannot currently record — see `services/store.py`.

        A dataset that cannot be read is listed with the reason rather than omitted. A
        partial write looks exactly like that, and knowing something unreadable is sitting
        there is the useful half.
        """
        from tendon.services.store import human_size, list_datasets

        return [
            {
                "ref": dataset.ref,
                "episodes": dataset.episodes,
                "size_bytes": dataset.size_bytes,
                "size": human_size(dataset.size_bytes),
                "modified": dataset.modified.isoformat(),
                "readable": dataset.readable,
                "detail": dataset.unreadable_because,
            }
            for dataset in list_datasets()
        ]

    # -------------------------------------------------------------------- sessions

    @app.post("/api/sessions")
    async def start_session(request: StartRequest) -> dict[str, Any]:
        """Start an episode. Refuses rather than queueing when one is already running.

        Two episodes on one body would fight over it, and interleaving them silently is
        worse than refusing.
        """
        from tendon.api.session import EpisodeSession
        from tendon.kernel.bus import Bus
        from tendon.kernel.scheduler import Scheduler, StepRecord
        from tendon.services.adaptive import AdaptivePolicy, StochasticPolicy, UncertainRegion
        from tendon.services.bodies import BodyUnavailable, PhysicalBodyRefused, open_body
        from tendon.services.limits import LocalLimitsError
        from tendon.services.memory_store import load_memory
        from tendon.services.policies import sine_sweep
        from tendon.services.skill import (
            IncompatibleBody,
            SkillError,
            load_skill,
            require_compatible,
        )

        try:
            # `root=` rather than the default: the discovery routes above already resolve
            # under the injected root, and this one went to the module global. An app
            # pointed at a fixture directory still started sessions from whatever was in
            # `skills/`, which made the injection look effective while doing nothing here.
            loaded = load_skill(request.skill, root=root)
        except SkillError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            body = open_body(request.body, allow_physical=request.allow_physical)
        except BodyUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PhysicalBodyRefused as exc:
            # 403 rather than 500: the request was understood and deliberately refused.
            # Letting this escape as a server error made a safety decision look like a bug
            # and left the shell with nothing to show the operator.
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        try:
            require_compatible(loaded, body)
        except IncompatibleBody as exc:
            body.close()
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        capability = body.capability

        # The machine's ceiling over whatever the skill asked for. `SECURITY.md`: a skill
        # declares its own limits, so an installed skill proposes the bounds it runs under,
        # and `tendon install` fetches from the Hub. Refused rather than run without: a site
        # that wrote a ceiling believes it has one, and a 500 here is better than a robot
        # moving under limits nobody chose.
        try:
            limits = _effective(loaded)
        except LocalLimitsError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        holder: dict[str, Any] = {}

        def make_policy():
            inner = StochasticPolicy(
                sine_sweep(dof=capability.dof),
                control_hz=capability.control_hz,
                dof=capability.dof,
                regions=(UncertainRegion(joint=0, centre=0.12, width=0.03, magnitude=0.08),),
                reference_spread=0.004,
                # A body with a jaw needs the jaw commanded, or the action is a channel
                # narrower than what the recorder is set up to store.
                gripper=1.0 if capability.gripper.value != "none" else None,
            )
            key = (loaded.ref, capability.body_id)
            if key not in memories:
                memories[key] = load_memory(memory_root, loaded.ref, capability.body_id)
            memory = memories[key]

            # Kept so the scheduler can hand corrections back to it. The policy is built
            # on the episode thread, so this is the only reference anybody else gets.
            holder["policy"] = AdaptivePolicy(inner, memory=memory)
            return holder["policy"]

        bus: Bus[StepRecord] = Bus()
        recorder = _open_recorder(loaded, episode_root)

        def make_scheduler(handler, on_step):
            # `on_step` was accepted and dropped here for the whole life of the shell. The
            # session builds a `state` message out of every step and the shell has a case
            # for it; nothing ever sent one, so a running episode moved a body while the
            # view that exists to show it stayed still.
            bus.subscribe("shell-stream", on_step)
            if recorder is not None:
                recorder.attach_to(bus)

            return Scheduler(
                driver=body,
                limits=limits,
                confidence_threshold=loaded.confidence_threshold,
                handler=handler,
                on_intent=lambda obs, intent: holder["session"].publish_intent(obs, intent),
                # Where a correction becomes something the policy knows. Wired only in
                # `examples/04_improve` until now — so the graph in the README was
                # produced by a script, while the interface an actual operator uses threw
                # every correction away the moment the motion finished. That is the claim
                # of this project, missing at the one place a human touches it.
                on_intervention=lambda obs, resolution: _learn_and_keep(
                    holder["policy"], memory_root, loaded.ref, capability.body_id, obs, resolution
                ),
                bus=bus,
                # Asked between chunks. `SECURITY.md` names this as required work before a
                # physical body is driven from the shell: an episode that loses its last
                # operator can no longer be answered if it asks for help, so it stops
                # proposing new motion — after finishing what was already committed,
                # because a stop that cuts a chunk short is itself a motion nobody chose.
                stop_when=lambda: holder["session"].abandoned(),
            )

        session = EpisodeSession(
            skill=loaded.ref,
            body_id=capability.body_id,
            scheduler_factory=make_scheduler,
            policy_factory=make_policy,
            max_steps=request.max_steps,
            seed=request.seed,
            timeout_s=request.timeout_s,
            before_episode=(
                None if recorder is None else lambda: recorder.start(loaded.ref, capability)
            ),
            after_episode=None if recorder is None else recorder.finish,
            # Two things happen when an operator decides. `note_interrupt` writes it into
            # the episode — the most valuable rows in the store, because demonstration data
            # almost never contains recovery from failure. And the memory is persisted, so
            # what they just taught survives a restart.
            #
            # Persisted here rather than at the end of the episode: a correction is a thing
            # a person did, and an episode that crashes afterwards should not take it with
            # it. Writing at human timescale is nowhere near the control loop.
            on_resolved=lambda context, resolution: _record_decision(
                recorder, context, resolution, loaded.ref
            ),
            # One line per finished episode. This is the only place that knows both how
            # often the policy asked and how much it had been taught by then, which are
            # the two axes of the graph the roadmap says v0.3 is measured by.
            on_result=lambda result: _record_progress(
                progress_root, loaded.ref, capability.body_id, memories, result
            ),
            # The body is opened here and handed to a thread. Until now it was closed only
            # when starting failed, so every episode that ran left one open — a MuJoCo
            # model each time, and a serial port on a physical arm, which is the failure
            # that stops the *next* session rather than this one. `tendon run` has always
            # closed it in a `finally`; the API had no equivalent.
            on_closed=body.close,
        )
        holder["session"] = session
        # Said rather than discovered. Without the recording extra an operator can hand
        # over, correct, watch the memory grow — and find `Episodes` empty afterwards,
        # with nothing having warned them. The command line has printed this since the
        # recorder was wired; the shell had no way to know.
        session.state.recording = recorder is not None

        try:
            registry.add(session)
        except RuntimeError as exc:
            body.close()
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        session.start()
        return session.snapshot()

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return [s.snapshot() for s in registry.all()]

    @app.get("/api/sessions/{session_id}")
    async def session_detail(session_id: str) -> dict[str, Any]:
        session = registry.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id}")

        snapshot = session.snapshot()
        pending = session.handler.pending
        snapshot["pending"] = pending.model_dump(mode="json") if pending else None
        return snapshot

    @app.post("/api/sessions/{session_id}/decide")
    async def decide(session_id: str, request: DecisionRequest) -> dict[str, Any]:
        """Answer a pending interrupt.

        A decision for an interrupt that is no longer pending is accepted and ignored: a
        second click on Approve is a person being unsure, not an error.
        """
        from tendon.kernel.types import Intent, InterruptResolution, Resolution

        session = registry.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id}")

        try:
            resolution = Resolution(request.resolution)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown resolution {request.resolution!r}; expected one of "
                    f"{[r.value for r in Resolution]}"
                ),
            ) from exc

        correction = None
        if request.correction is not None:
            try:
                correction = Intent.model_validate(request.correction)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"invalid correction: {exc}") from exc

        if resolution is Resolution.CORRECTED and correction is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "a CORRECTED decision must carry a correction; approving what the "
                    "operator meant to replace is the dangerous reading"
                ),
            )

        accepted = session.handler.decide(
            InterruptResolution(resolution=resolution, correction=correction, note=request.note)
        )
        return {"accepted": accepted}

    @app.websocket("/ws/{session_id}")
    async def stream(websocket: WebSocket, session_id: str) -> None:
        """Live events for one episode.

        Polls the worker's thread-safe queue from the event loop. An asyncio queue would be
        wrong in the other direction: the scheduler thread cannot safely write to one, and
        getting that wrong produces a hang rather than an error.
        """
        await websocket.accept()
        session = registry.get(session_id)
        if session is None:
            await websocket.send_json({"type": "error", "detail": f"no session {session_id}"})
            await websocket.close()
            return

        # A viewer connecting mid-handover must see the decision it is being asked for,
        # rather than waiting for a next event that may never come.
        pending = session.handler.pending
        if pending is not None:
            await websocket.send_json(
                {"type": "interrupt", "context": pending.model_dump(mode="json")}
            )

        session.watching()
        try:
            while True:
                try:
                    event = session.events.get_nowait()
                except queue_module.Empty:
                    if session.state.finished and session.events.empty():
                        await websocket.send_json({"type": "finished", "state": session.snapshot()})
                        break

                    # Wait on the socket rather than on the clock. A disconnect used to be
                    # noticed only when the next `send_json` failed, so an idle stream did
                    # not notice at all — and the stream is idle exactly during a handover,
                    # when the policy has stopped producing steps and is waiting for the
                    # operator who has just gone. That is the case
                    # `EpisodeSession.abandoned` exists for, and it could not fire because
                    # the viewer was still counted.
                    #
                    # A closed socket surfaces two ways here: `WebSocketDisconnect` on the
                    # frame that carries the close, and `RuntimeError` on any receive after
                    # it. Both mean the same thing and only the first is an exception type
                    # anybody would think to catch.
                    with contextlib.suppress(TimeoutError):
                        try:
                            await asyncio.wait_for(websocket.receive(), _POLL_INTERVAL_S)
                        except RuntimeError:
                            break
                    continue
                await websocket.send_json(event)
        except WebSocketDisconnect:
            # Not a reason to stop the body *now*. Stopping abruptly is itself a motion
            # nobody chose, so the committed chunk finishes and the scheduler declines to
            # ask for another — see `EpisodeSession.abandoned`.
            return
        finally:
            # In `finally` so an error path cannot leave the count saying somebody is
            # watching. A viewer counted forever is a body that never learns it is alone.
            session.stopped_watching()

    # --------------------------------------------------------------------- the shell

    # Mounted last so /api and /ws win. Served only when a build exists: during
    # development the Vite dev server proxies here instead, and mounting a stale dist
    # underneath it would serve yesterday's interface to someone who just edited it.
    if _SHELL_DIST.is_dir():
        app.mount("/", StaticFiles(directory=_SHELL_DIST, html=True), name="shell")

    return app
