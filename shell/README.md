# shell/ — where a human watches and intervenes

The web interface. This is where tendon differentiates, so it gets the most attention.

Three responsibilities:

1. **Intent preview** — render what the robot is *about to* do: upcoming trajectory,
   target object, predicted outcome, confidence. Before it moves, not after.
2. **Intervention** — approve, reject, or correct in natural language. A rejection
   asks the policy for alternatives rather than cutting power.
3. **Review** — browse episodes, see where interventions happened, watch the
   intervention rate fall over time.

## Stack

React + TypeScript + Vite. Talks to `src/tendon/api` over REST and WebSocket.
3D and time-series views embed the [Rerun web viewer](https://rerun.io) rather than
reimplementing a renderer.

## Design rule

An operator has seconds to decide, often on a tablet, often on a factory floor.
Every screen answers one question: **what is it about to do, and should I let it?**
If a control does not serve that question, it does not belong on the main view.
