# Architecture

```
+-------------------------------------------------------------+
|  APPS       task workspaces                                  |
+-------------------------------------------------------------+
|  SHELL      intent preview - confidence - approve/reject      |
|             natural language correction        [shell/]      |
+-------------------------------------------------------------+
|  SERVICES   recorder - curator - trainer - evaluator          |
|             registry                    [src/tendon/services]|
+-------------------------------------------------------------+
|  KERNEL     scheduler - action bus - interrupt - safety       |
|                                         [src/tendon/kernel]  |
+-------------------------------------------------------------+
|  DRIVERS    embodiment HAL                                    |
|             mujoco - lerobot - so101 - human                  |
|                                         [src/tendon/drivers] |
+-------------------------------------------------------------+
```

## Control flow

```
skill  ->  policy  --intent-->  [safety]  -->  driver  -->  body
                                    |                         |
                                    |          observation <--+
                                    v
                              confidence low?
                                    |
                                yes | INT
                                    v
                    shell  <-->  operator  -->  correction
                                    |
                                    v  IRET
                              resume + record
```

Every action passes `safety` before reaching a driver, including actions produced during an
intervention. An operator can correct a policy but cannot exceed a hard limit.

## Data flow

```
execution --> recorder --> episode store (LeRobotDataset + sidecar)
                                |
                                v
                            curator  --> selected episodes
                                            |
                                            v
                                        trainer (LoRA)
                                            |
                                            v
                                       skill vN+1 --> evaluator
```

The sidecar table holds what LeRobotDataset does not model: confidence traces, interrupt
spans, operator corrections, curation scores. Keyed by episode and frame index, joined at
read time. See [ADR 0001](decisions/0001-record-in-lerobot-format.md).

## Two clocks

The kernel scheduler runs two tiers, because a large model cannot meet a control deadline.

| Tier | Rate | Runs | Produces |
| --- | --- | --- | --- |
| Deliberation | ~1-10Hz | VLA policy | action chunk, ~0.5-1s of intent |
| Control | 100Hz+ | driver-side controller | interpolated setpoints |

This is also what makes the shell possible. The action chunk is the artifact an operator
reviews, and it exists because of a latency constraint rather than for the interface.

## Module boundaries

| Layer | May import | Must not import |
| --- | --- | --- |
| kernel | pydantic, numpy | torch, mujoco, any driver, any service |
| drivers | its own backend | services, shell |
| services | torch, transformers, peft, lerobot | kernel internals |
| api | kernel, services | driver internals |
| shell | api over HTTP and WebSocket | anything Python |

Violations are checked in `tests/unit/test_boundaries.py`. The kernel importing a training
dependency is the failure mode that would quietly turn this project back into a monolith.

## Deployment shape

One robot host runs the kernel, drivers and services. The shell is served from that host and
opened on an operator tablet or workstation. Nothing requires a cluster; the nightly LoRA
run is sized for a single consumer GPU, because the loop has to close every night to be real.
