# skills/ — installable robot capabilities

A skill is a policy plus everything needed to run and evaluate it:

```
skills/<namespace>/<name>/
  skill.yaml        identity, version, required driver capabilities, safety limits
  policy/           weights reference (Hub id) or LoRA adapter
  eval/             success criteria and evaluation episodes
  README.md
```

Skills are distributed through the Hugging Face Hub. tendon does not run its own
registry — `skill.yaml` points at a Hub repo, and `tendon install` resolves it.

**Lives here:** reference skills maintained by this repo.
**Does not live here:** your site-specific forks. Those live in your own repo.
