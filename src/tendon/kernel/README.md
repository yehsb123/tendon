# kernel/ — the policy runtime

Thin by design. The kernel owns four things and nothing else.

| Module | Responsibility |
| --- | --- |
| `scheduler` | Which policy runs at which rate. Slow deliberation (~10Hz) and fast control (~200Hz+) are separate tiers. |
| `bus` | The action bus. Policies publish intent; drivers subscribe. Neither knows the other. |
| `interrupt` | Design decision 2. Raise on low confidence or a safety trip, hand control to a human with context preserved, resume. |
| `safety` | Hard limits checked on every action, independent of the policy. Workspace bounds, force ceilings, velocity caps. |

## Invariants

- The kernel never imports `torch`, `mujoco`, or any driver.
- Every action that reaches a driver has passed `safety`.
- An interrupt preserves enough state to resume; if it cannot, it is a fault, not an interrupt.
- Losing the shell connection must never leave the robot mid-motion.
