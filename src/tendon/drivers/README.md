# drivers/ — the embodiment HAL

Design decision 3. One module per body. Policies address intent; drivers translate.

```
base.py       registration, negotiation, faults (the protocol itself lives in
              kernel/protocols.py - the kernel owns the contract)
mujoco.py     simulation — the only driver v0.1 needs
lerobot.py    anything LeRobot already supports
so101.py      the SO-101 arm on the bench
human.py      human demonstration video as a read-only body
```

## The contract

A driver declares its capabilities (degrees of freedom, gripper type, available
sensors, control rate) and implements two directions:

- **down** — accept a uniform action, produce whatever this body needs
- **up** — report observation and proprioceptive state in a uniform shape

`human.py` is read-only: it produces observations and actions from recorded video
but accepts no commands. This is what makes human demonstrations and robot episodes
sit in the same dataset.

## Rule

If a policy needs to know which driver is loaded, the abstraction has failed.
Capability negotiation happens once at load time, never inside the control loop.
