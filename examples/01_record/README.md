# 01_record — running is collecting

Run a policy in MuJoCo and watch episodes appear without enabling anything.

**Proves:** design decision 1.
**Needs:** `pip install "tendon-os[sim]"`. No GPU, no hardware.

**Check:** the control loop rate with the recorder on and off. If they differ meaningfully,
this example has found a bug that matters more than the example.
