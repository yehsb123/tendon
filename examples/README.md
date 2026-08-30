# examples/ — end to end, runnable

Each example is a complete scenario that runs from a clean checkout. If an example
needs a step that is not in its README, the example is broken.

Ordered by what they prove:

```
01_record/       run a policy in MuJoCo, watch episodes appear      (decision 1)
02_preview/      see the intended trajectory before it executes     (decision 3 + shell)
03_intervene/    interrupt, correct, resume — and find it recorded  (decision 2)
04_improve/      fine-tune on corrections, measure intervention rate (the proof)
```

`04_improve` is the project. The other three exist to make it reachable.
