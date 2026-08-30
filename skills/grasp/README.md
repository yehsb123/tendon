# skills/grasp/

Reference grasping skills. The namespace exists to establish the layout before any skill is
real.

```
grasp/<name>/
  skill.yaml     identity, version, required capabilities, safety limits
  policy/        Hub reference or LoRA adapter
  eval/          success criteria and evaluation episodes
  README.md
```

Planned first skill: `grasp/cube-sim` — pick a cube in MuJoCo. Deliberately trivial. It
exists to exercise install, run, evaluate, fork and publish end to end, not to be impressive.
