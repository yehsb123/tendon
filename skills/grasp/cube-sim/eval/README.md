# eval/

What "it worked" means for this skill, stated before the policy is trained.

This directory holds no files. The success condition lives in `skill.yaml` under
`eval.success`, which is where `tendon eval` reads it from:

```yaml
eval:
  episodes: 50
  success:
    cube_height_above: 0.1
  report: [success_rate, intervention_rate, failure_modes]
```

An earlier version of this page described a `criteria.yaml` and an `episodes/` directory
beside it. Neither was ever written. Both are described below as they actually work.

## Fixed starting states

Fixed by seed rather than by committed files. `tendon eval --seed` defaults to 0 and each
episode runs at `seed + index`, so the same command twice visits the same starting states
in the same order.

Two policies evaluated from different random starts are not comparable, and the number
this project lives on — intervention rate over cumulative corrections — is only meaningful
if the denominator holds still.

## What is reported

Success rate is the number people ask for. The failure mode breakdown is the one that
changes what you do next, and it is what the shell shows.

```
success_rate         did the cube end above the height threshold
intervention_rate    how often a human had to take over
failure_modes        grip slipped / approach angle / missed entirely / timeout
```

A skill that reports only a success rate has hidden the useful half of the result.
