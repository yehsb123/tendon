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
import queue as queue_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tendon import __version__

__all__ = ["create_app"]

#: Where skills are looked for when no explicit root is configured.
_DEFAULT_SKILL_ROOT = Path("skills")

#: How often the socket checks the worker queue when it is empty [s].
_POLL_INTERVAL_S = 0.02

#: Built shell assets, served when they exist so one command is enough.
_SHELL_DIST = Path("shell") / "dist"


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


def create_app(*, skill_root: Path | None = None) -> FastAPI:
    """Build the API.

    `skill_root` is injected rather than read from a global so tests can point it at a
    fixture directory, and so a deployment can serve skills from somewhere other than the
    working directory.
    """
    from tendon.api.session import SessionRegistry

    root = skill_root if skill_root is not None else _DEFAULT_SKILL_ROOT
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
        from tendon.services.skill import SkillError, load_skill

        found: list[dict[str, Any]] = []
        for path in sorted(root.glob("*/*/skill.yaml")):
            try:
                loaded = load_skill(path)
            except SkillError as exc:
                found.append({"ref": str(path.parent), "error": str(exc)})
                continue

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
                    "safety": loaded.limits.model_dump(),
                    "policy_base": loaded.policy_base,
                }
            )
        return found

    @app.get("/api/skills/{namespace}/{name}")
    async def skill_detail(namespace: str, name: str) -> dict[str, Any]:
        from tendon.services.skill import SkillError, load_skill

        try:
            loaded = load_skill(root / namespace / name / "skill.yaml")
        except SkillError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return {
            "ref": loaded.ref,
            "version": loaded.version,
            "summary": loaded.summary,
            "license": loaded.license,
            "confidence_threshold": loaded.confidence_threshold,
            "safety": loaded.limits.model_dump(),
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
        from tendon.kernel.scheduler import Scheduler
        from tendon.services.adaptive import AdaptivePolicy, StochasticPolicy, UncertainRegion
        from tendon.services.bodies import BodyUnavailable, PhysicalBodyRefused, open_body
        from tendon.services.policies import sine_sweep
        from tendon.services.skill import (
            IncompatibleBody,
            SkillError,
            load_skill,
            require_compatible,
        )

        try:
            loaded = load_skill(request.skill)
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
        holder: dict[str, Any] = {}

        def make_policy():
            inner = StochasticPolicy(
                sine_sweep(dof=capability.dof),
                control_hz=capability.control_hz,
                dof=capability.dof,
                regions=(UncertainRegion(joint=0, centre=0.12, width=0.03, magnitude=0.08),),
                reference_spread=0.004,
            )
            return AdaptivePolicy(inner)

        def make_scheduler(handler, on_step):
            return Scheduler(
                driver=body,
                limits=loaded.limits,
                confidence_threshold=loaded.confidence_threshold,
                handler=handler,
                on_intent=lambda obs, intent: holder["session"].publish_intent(obs, intent),
            )

        session = EpisodeSession(
            skill=loaded.ref,
            body_id=capability.body_id,
            scheduler_factory=make_scheduler,
            policy_factory=make_policy,
            max_steps=request.max_steps,
            seed=request.seed,
            timeout_s=request.timeout_s,
        )
        holder["session"] = session

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

        try:
            while True:
                try:
                    event = session.events.get_nowait()
                except queue_module.Empty:
                    if session.state.finished and session.events.empty():
                        await websocket.send_json({"type": "finished", "state": session.snapshot()})
                        break
                    await asyncio.sleep(_POLL_INTERVAL_S)
                    continue
                await websocket.send_json(event)
        except WebSocketDisconnect:
            # Losing a viewer is not a reason to stop the body. The episode continues, and
            # a reconnecting shell receives the pending interrupt on connect.
            return

    # --------------------------------------------------------------------- the shell

    # Mounted last so /api and /ws win. Served only when a build exists: during
    # development the Vite dev server proxies here instead, and mounting a stale dist
    # underneath it would serve yesterday's interface to someone who just edited it.
    if _SHELL_DIST.is_dir():
        app.mount("/", StaticFiles(directory=_SHELL_DIST, html=True), name="shell")

    return app
