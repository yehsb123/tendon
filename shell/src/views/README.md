# views/ — full screens

```
Live         a running episode: scene, intent, intervention     (the default)
Progress     whether the intervention rate is falling, against corrections taught
Episodes     browse recorded runs
Skills       what is installed, and the terms each skill runs under
Curate       recorded episodes ranked by what is worth training on, with reasons
Training     nightly LoRA runs                                  (not built)
```

`Live` is the only view an operator sees during a shift. The others are for the person
improving the system afterwards, and may be as dense as they need to be.

`Training` is the only one with no file behind it: `services/trainer.py` is not wired, and
`tendon train` says so. It used to be described here as the place the intervention rate
would be shown — `Progress` does that now, from the running system rather than from a
nightly job that does not exist.

That split is deliberate: designing one interface for both readers is how monitoring
tools become unusable in the situation they were built for.
