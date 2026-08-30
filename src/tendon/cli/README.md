# cli/ — the `tendon` command

Typer. Mirrors the OS metaphor so the commands are guessable.

```
tendon run <skill> --driver mujoco     start a policy under the kernel
tendon shell                           serve the intervention interface
tendon episodes                        list, inspect, export recorded runs
tendon curate                          score and select episodes
tendon train <skill>                   LoRA fine-tune on curated data
tendon eval <skill>                    run the evaluation set
tendon install|fork|publish <skill>    skill package management
tendon doctor                          check drivers, GPU, disk, Hub auth
```

Every command must work against the MuJoCo driver with no hardware attached.
