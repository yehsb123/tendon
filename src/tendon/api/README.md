# api/ — the surface the shell talks to

FastAPI. Two channels, deliberately separated:

- **REST** — skills, bodies, sessions, decisions. Request/response.
- **WebSocket** — live intent, state, interrupt raise and resolve. Push.

Both live in `app.py`, and `session.py` bridges the synchronous scheduler to them. There
is no separate `ws.py`: a message contract written beside the code that sends it stays
true, and one written in its own file drifts. The shell's half is `shell/src/api/socket.ts`,
which is checked against this by nothing — so when a message type is added, both move.

The intent stream is latency-critical: an operator has to see what the robot is
about to do while it is still about to do it. Anything that can be precomputed
belongs to REST so the socket carries only what must be live.

The API is a boundary, not a place for logic. If a handler is doing more than
translating between HTTP and a kernel or service call, the logic is in the wrong layer.
