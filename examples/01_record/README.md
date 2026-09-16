# 01_record — running is collecting

Run a policy in MuJoCo and watch episodes appear without enabling anything.

**Proves:** design decision 1.
**Needs:** `[sim]` and `[robot]`. No GPU, no hardware.

## Run it

```bash
python -m pip install -e ".[sim,robot,dev]"

python examples/01_record/run.py
```

It runs the same episode twice, once with the recorder subscribed and once without, and
reports what the recorder cost per control step. Then:

```bash
tendon episodes    # the same store, from the CLI
```

## What it is for

There is no `--record` flag anywhere in tendon, and that is the decision this example
exists to make concrete. A flag would be off during the run that mattered, and the runs
that matter most are failures.

**Check:** the control loop rate with the recorder on and off. If they differ meaningfully,
this example has found a bug that matters more than the example.
