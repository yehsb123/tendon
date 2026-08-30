# api/ — typed client for the runtime

```
types.ts      mirrors src/tendon/kernel/types.py — the shared vocabulary
rest.ts       episodes, skills, training runs, evaluation results
socket.ts     live intent, confidence, interrupt raise and resolve
```

## The mirror has to be exact

`types.ts` restates Pydantic models by hand, which is a maintenance cost accepted on
purpose: generating them would add a build step to a project that has to stay easy to run.

The cost is paid by a test. `tests/unit/test_api_contract.py` reads both sides and fails
when a field exists in one and not the other. A silent divergence here shows up as an
operator seeing stale confidence during an intervention, which is the worst possible place
to find it.

## Socket discipline

The socket carries only what must be live. Anything precomputable belongs to REST.

Losing the connection must never leave the body mid-motion: the control tier holds
position and the deliberation tier stops issuing new intent until the shell returns. The
client surfaces that state rather than hiding it — an operator who cannot tell a frozen
robot from a frozen UI will reach for the physical stop, which destroys the context this
whole design exists to preserve.
