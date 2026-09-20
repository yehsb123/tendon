# cli/ — the `tendon` command

Typer. Mirrors the OS metaphor so the commands are guessable.

```
tendon run <skill> --driver mujoco     start a policy under the kernel
tendon shell                           serve the intervention interface
tendon episodes                        list, inspect, export recorded runs
tendon curate                          score and select episodes
tendon train <skill>                   LoRA fine-tune on curated data
tendon eval <skill>                    run the evaluation set
tendon calibrate <skill>               measure what disagreement is typical for a policy
tendon progress <skill>                plot intervention rate against corrections taught
tendon doctor                          check drivers, GPU, disk, Hub auth
tendon install <skill>                 # v0.4
tendon fork <skill>                    # v0.4
tendon publish <skill>                 # v0.4
```

Everything above the `v0.4` lines exists and is runnable now. The three below do not, and
listing them as `install|fork|publish` beside nine working commands said otherwise.

Every command must work against the MuJoCo driver with no hardware attached.
