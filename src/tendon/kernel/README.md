# kernel/ — the policy runtime

Thin by design. The kernel owns four things and nothing else.

| Module | Responsibility |
| --- | --- |
| `scheduler` | Which policy runs at which rate. Slow deliberation (~10Hz) and fast control (~200Hz+) are separate tiers. |
| `bus` | The step bus. The scheduler publishes each control step; the recorder and the shell stream subscribe. A subscriber that raises is isolated and dropped — none of them is a reason to stop a moving body. |
| `interrupt` | Design decision 2. Raise on low confidence or a safety trip, hand control to a human with context preserved, resume. |
| `safety` | Hard limits checked on every action, independent of the policy. Workspace bounds, force ceilings, velocity caps. |

The other two modules are not responsibilities and are listed because a reader looking for
`Driver` or `Action` should not have to guess:

| Module | Responsibility |
| --- | --- |
| `types` | The vocabulary. `Action`, `Observation`, `Intent`, `Confidence`, `SafetyLimits`, `Capability` — what every layer above agrees these words mean. |
| `protocols` | The contracts. `Driver` and `Policy`, structural rather than inherited, so a body or a policy can satisfy them without importing tendon. |

## Invariants

- The kernel never imports `torch`, `mujoco`, or any driver.
- Every action that reaches a driver has passed `safety`.
- An interrupt preserves enough state to resume; if it cannot, it is a fault, not an interrupt.
- Losing the shell connection must never leave the robot mid-motion.
- A bus subscriber can never stop the robot. A recorder that fills the disk is
  isolated and dropped, and the failure is reported on the episode result.
