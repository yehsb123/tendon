---
name: Bug report
about: Something behaves differently than documented
labels: bug
---

## What happened

## What was expected

## Reproduction

```
tendon ...
```

## Environment

- tendon version:
- driver (mujoco / so101 / other):
- OS and Python:
- GPU, if relevant:

## Safety

- [ ] This involved physical hardware
- [ ] Something moved unexpectedly

If either is checked, describe the state the body was in and whether a safety limit or
an interrupt fired. A motion that should have been caught by `kernel/safety` and was not
is the highest-severity class of bug in this project, ahead of any crash.
