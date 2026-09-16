# 04_improve — correction becomes learning

Correct a policy where it is unsure, and measure whether it asks less often afterwards.

**Proves:** that the loop closes — the machinery, not that a model improves. See below.
**Needs:** `[sim]` only. **No GPU.** This README asked for `[train]` and a consumer GPU for
months, which was wrong in the direction that costs most: it turned the project's headline
example away from anybody without one.

## Run it

```bash
python -m pip install -e ".[sim,dev]"

python examples/04_improve/run.py
python examples/04_improve/run.py --episodes 80 --out results/
```

It runs episodes, corrects the policy wherever confidence falls, and prints the curve:
cumulative corrections on x, intervention rate on y.

## Read both numbers

The line falls. It also falls for a policy that stopped *trying*, and those are the same
picture — so the run prints the success rate beside it, and for this policy that rate is
**0% throughout**. The operator here corrects a joint sweep; it does not reach for the cube
and was never going to.

So what this proves is the machinery: an interrupt raised before motion, a human decision
recorded, later behaviour changed by it, and the change attributable to the teaching rather
than to anything else. **Read it as "the loop runs", never as "the loop learns".**

Swapping in a trained policy and a real operator is the v0.3 experiment. That one does need
a GPU, and it is `tendon train` plus `tendon run --policy adapter`, not this file.

**Check:** the graph. If the line is flat, the loop does not close, and that result belongs
in the README as prominently as a success would.
