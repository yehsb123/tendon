# api/ — the surface the shell talks to

FastAPI. Two channels, deliberately separated:

- **REST** — episodes, skills, training runs, evaluation results. Request/response.
- **WebSocket** — live intent stream, confidence, interrupt raise and resolve. Push.

The intent stream is latency-critical: an operator has to see what the robot is
about to do while it is still about to do it. Anything that can be precomputed
belongs to REST so the socket carries only what must be live.

The API is a boundary, not a place for logic. If a handler is doing more than
translating between HTTP and a kernel or service call, the logic is in the wrong layer.
