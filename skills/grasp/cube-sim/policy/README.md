# policy/

No weights are committed to this repository.

`skill.yaml` names a base policy on the Hugging Face Hub (`lerobot/smolvla_base`), and
`tendon install` resolves it. Weights are large, versioned upstream, and already hosted;
copying them here would make the repository heavy and the provenance worse.

After `tendon train`, a LoRA adapter appears in this directory or as a Hub reference in
`skill.yaml`. Adapters are small enough to version per site, which is the whole reason the
trainer uses LoRA rather than full fine-tuning — see docs/stack.md.

```
adapter/            LoRA weights, when one exists
adapter_config.json PEFT config
```
