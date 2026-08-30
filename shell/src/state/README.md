# state/ — session and connection

Zustand. Three stores, deliberately separate.

| Store | Holds |
| --- | --- |
| `connection` | socket status, reconnect attempts, last heartbeat |
| `episode` | the running episode, current step, confidence history |
| `pending` | the intent awaiting a decision, and the decision being made |

`pending` is separate because it outlives a reconnect. An operator who was mid-decision
when the socket dropped must come back to the same decision, not to an empty screen.
