"""The surface the shell talks to.

Two channels, deliberately separated:

    REST       episodes, skills, training runs, evaluation results
    WebSocket  live intent, confidence, interrupt raise and resolve

The intent stream is latency-critical: an operator has to see what the robot is about to
do while it is still about to do it. Anything precomputable belongs to REST so the socket
carries only what must be live.

This module is a boundary, not a place for logic. A handler doing more than translating
between HTTP and a kernel or service call means the logic is in the wrong layer.
"""

from __future__ import annotations

from fastapi import FastAPI

from tendon import __version__


def create_app() -> FastAPI:
    app = FastAPI(
        title="tendon",
        version=__version__,
        summary="The operating layer for physical AI",
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app
