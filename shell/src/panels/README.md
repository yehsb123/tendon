# panels/ — composable pieces

```
IntentPreview      what the robot is about to do        <- the centre of the project
ConfidenceMeter    how sure it is, and why not
InterruptPrompt    approve / reject / correct
AlternativeList    other trajectories, when one is rejected
EpisodeTimeline    a run with its interruptions marked
InterventionRate   the graph that decides the project
```

## IntentPreview

Everything else supports this. It renders an action chunk before execution: the
trajectory, the target, and the predicted outcome, over the live scene.

Its hard constraint is the operator, not the framework. Someone on a factory floor has a
couple of seconds and one hand free. If a change makes the panel more informative but
slower to read, it is a regression.

Three states it must handle without a layout shift, because a shifting layout costs the
operator the seconds the panel exists to save:

| State | Shows |
| --- | --- |
| Confident | trajectory, quiet, no prompt |
| Uncertain | trajectory plus the reasons confidence is low |
| Interrupted | frozen scene, saved context, the decision controls |
