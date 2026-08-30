# src/ — the tendon runtime

The Python package that runs on the robot host.

```
src/tendon/
  kernel/     policy runtime: scheduler, step bus, interrupts, safety
  drivers/    embodiment HAL — one module per body (mujoco, lerobot, so101, human)
  services/   background daemons: recorder, curator, trainer, evaluator
  api/        FastAPI surface the shell talks to
  cli/        the `tendon` command
```

**Lives here:** anything that runs headless on the robot host.

**Does not live here:** UI code (`shell/`), skill definitions (`skills/`),
runnable demos (`examples/`).

## Rule: the kernel stays thin

The kernel schedules, routes actions, raises interrupts, and enforces safety limits.
It does not know what a policy is made of, how a robot is wired, or how anything is
trained. Those belong to drivers and services, which are swappable.

If you find yourself importing `torch` in `kernel/`, something is in the wrong place.
