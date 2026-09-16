# 02_preview — intent before motion

Watch the action chunk render as a trajectory *before* it executes.

**Proves:** design decision 3, and that a plan is legible to a human.
**Needs:** `[sim]` and the shell built.

This example has no `run.py`, and that is not an oversight: what it proves is that a person
can read a plan. No assertion settles that. What follows are the steps, because
`examples/README.md` says an example needing a step not in its README is broken — and for
a while these instructions said "open the shell" without saying how.

## Run it

```bash
python -m pip install -e ".[sim,robot,dev]"

cd shell && npm install && npm run build && cd ..

tendon serve
```

`serve` prints the address and whether it found the built interface. Open the address it
prints — `http://127.0.0.1:8000` unless you passed `--port`.

Then, in the **Live** view:

1. Choose skill `grasp/cube-sim` and body `mujoco`.
2. Press start.
3. Watch the panel above the body. Each chunk appears as a trajectory while the previous
   one is still executing.

## What you are looking at

The policy predicts roughly fifty steps at a time and the body executes them at 100 Hz, so
the plan is drawn a moment before it is performed. That gap is the whole point: an operator
who can only react *after* a motion has nothing to approve.

**Check:** can you decide to allow or stop it within roughly two seconds of looking? If not,
the preview is showing the wrong thing — and that is a finding about the view, not about
your reading speed.
