"""The surface the shell talks to.

Two channels, deliberately separated:

    REST       skills, bodies, episodes, evaluation results
    WebSocket  live intent, confidence, interrupt raise and resolve

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

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from tendon import __version__

__all__ = ["create_app"]

#: Where skills are looked for when no explicit root is configured.
_DEFAULT_SKILL_ROOT = Path("skills")


def create_app(*, skill_root: Path | None = None) -> FastAPI:
    """Build the API.

    `skill_root` is injected rather than read from a global so tests can point it at a
    fixture directory, and so a deployment can serve skills from somewhere other than the
    working directory.
    """
    root = skill_root if skill_root is not None else _DEFAULT_SKILL_ROOT

    app = FastAPI(
        title="tendon",
        version=__version__,
        summary="The operating layer for physical AI",
    )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """Liveness, and enough to tell which runtime the shell is talking to.

        The shell shows the version because a shell built against a different contract is
        the failure that looks like a bug everywhere else.
        """
        return {"status": "ok", "version": __version__}

    @app.get("/api/bodies")
    async def bodies() -> list[dict[str, Any]]:
        """Bodies this runtime can load.

        Reports what is registered rather than what is installed: a driver whose backend
        is missing never registers, so this is the honest list.
        """
        from tendon.services.bodies import discover

        return [
            {"name": info.name, "available": info.available, "detail": info.unavailable_because}
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

        path = root / namespace / name / "skill.yaml"
        try:
            loaded = load_skill(path)
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

    return app
