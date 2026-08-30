# eval/

What "it worked" means for this skill, stated before the policy is trained.

```
criteria.yaml   success condition, from skill.yaml eval block
episodes/       fixed starting states, committed so results are comparable
```

## Fixed starting states

The evaluation episodes are committed rather than sampled at run time. Two policies
evaluated against different random seeds are not comparable, and the number this project
lives on — intervention rate over cumulative corrections — is only meaningful if the
denominator holds still.

## What is reported

Success rate is the number people ask for. The failure mode breakdown is the one that
changes what you do next, and it is what the shell shows.

```
success_rate         did the cube end above the height threshold
intervention_rate    how often a human had to take over
failure_modes        grip slipped / approach angle / missed entirely / timeout
```

A skill that reports only a success rate has hidden the useful half of the result.
