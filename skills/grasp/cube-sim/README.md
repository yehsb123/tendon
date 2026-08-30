# grasp/cube-sim

Pick up a cube in MuJoCo.

Deliberately trivial. This skill exists to exercise the package format end to end —
install, run, evaluate, fork, publish — not to be impressive. If the format cannot express
picking up a cube, it cannot express anything harder.

```
skill.yaml     identity, required capabilities, safety limits, interrupt threshold
policy/        weights reference, and any LoRA adapter produced by `tendon train`
eval/          success criteria and the episodes evaluated against
```

## Why the numbers in skill.yaml look arbitrary

They are, and that is recorded rather than hidden. `confidence_threshold: 0.5` is a guess.
Confidence is not calibrated across skills, so a threshold that is right for one is noise
for another. Per-skill calibration is v0.3 work. Until then an operator moves this number
and the value here is a starting point, not a recommendation.

The safety limits are not a guess. They bound the MuJoCo scene, and `kernel/safety`
enforces them on every action including operator corrections.
