# third_party, what was ported, and what was surveyed instead

`docs/stack.md` states the rule: depend where possible, port only what cannot be depended
on. This file records both sides of that decision, because a survey that lives only in
someone's head gets repeated.

Each ported directory carries its original `LICENSE` unmodified and a `PROVENANCE.md`
naming the upstream commit, what was taken, what was changed, and why a dependency was
not enough. CI fails without both.

---

## Ported

| Directory | Upstream | Commit | Licence | Size |
| --- | --- | --- | --- | --- |
| `mujoco_menagerie/trs_so_arm100/` | [google-deepmind/mujoco_menagerie](https://github.com/google-deepmind/mujoco_menagerie) | `da76818` | Apache-2.0 | 3.1 MB |

Why ported. Assets, not code. `pip install mujoco` ships a physics engine and no robot
models; Menagerie is distributed only as a git repository, so there is nothing to depend
on. Taking one model directory out of a ~570 MB repository keeps the clone-and-run promise
in `CONTRIBUTING.md` intact.

Note that Menagerie as a whole is `NOASSERTION` on GitHub, because each model carries its
own author's terms. Only `trs_so_arm100/LICENSE` applies here, and it is Apache-2.0.
Nothing else from that repository is vendored.

One derived file lives outside this directory:
`src/tendon/assets/robots/so_arm100_wrist_cam.xml` is the model with a single
`<camera name="wrist">` added, carrying the modification notice Apache-2.0 §4(b) requires.
It is kept as a one-element patch so an upstream refresh stays a two-minute re-apply.
Details in `mujoco_menagerie/PROVENANCE.md`.

---

## Surveyed and depended on, not ported

Read at the versions below by opening the source, not the documentation. Findings that
changed a tendon decision are marked.

### LeRobot, `0.6.x`, commit `4aaff99`, Apache-2.0

The single most important dependency. Robot drivers, the dataset format, policy
implementations, and, as it turns out, a good deal more.

| What it gives | Where tendon uses it |
| --- | --- |
| `LeRobotDataset` — parquet + mp4, appendable during live execution | `services/recorder.py` writes it; `drivers/human.py` reads it |
| `Robot` ABC and hardware drivers (SO-100/101 and others) | the v0.4 `so101` driver wraps it |
| ~20 policy implementations | skills reference them; no weights in this repo |
| `PreTrainedPolicy.wrap_with_peft()` | LoRA, for `services/trainer.py` |
| `utils/rerun_visualization.py` | the shell's live view |
| `policies/rtc/` — Real-Time Chunking | see below |
| `rollout/strategies/` — sentry, dagger, episodic, highlight | see below |

Install note that costs an hour if missed. `pip install lerobot` alone cannot open a
dataset. `LeRobotDataset` needs lerobot's own `dataset` extra, which pins `av>=15,<16`; a
bare `pip install av` resolves to 18.x and fails with
`module 'av' has no attribute 'option'`. On Windows there is no `torchcodec` wheel, so
LeRobot falls back to pyav with a warning, it works, and reads are slower.

Findings that changed a decision:

1. `policies/rtc/` is the two-clock problem, already solved and published. Real-Time
   Chunking, from [arXiv 2506.07339](https://arxiv.org/abs/2506.07339) (Black, Galliker,
   Levine) by way of Physical Intelligence's openpi. Not a policy, an inference-time
   technique for executing action chunks under latency. Ships `action_queue.py`,
   `action_interpolator.py`, `latency_tracker.py`. That is the machinery
   `docs/architecture.md` describes `kernel/scheduler.py` as needing. Applies to
   flow-matching policies; `PreTrainedPolicy.supports_rtc` says which qualify.
2. `rollout/strategies/` overlaps design decisions 1 and 2. `sentry.py` records
   continuously with auto-upload. `dagger.py` implements RaC, an operator takes over
   mid-policy, corrections are tagged `intervention=True`, and actuated teleoperators are
   driven to the follower's last pose so the handover has no jerk. Its state machine is
   `AUTONOMOUS → PAUSED → CORRECTING`, against tendon's `RUNNING → PENDING → RESUMING`.
3. No policy reports confidence. `confidence` does not appear anywhere in `rollout/`
   or `policies/pretrained.py`. `select_action` and `predict_action_chunk` both return a
   bare tensor. Every handover upstream is human-initiated, a key or a foot pedal,
   pressed by someone already watching. This is where tendon's claim actually lives:
   the system raising its own hand, before the body moves. It also means the confidence
   estimator is a fifth thing tendon must build, against the four `docs/stack.md` names.
4. GR00T N1.5 is not future work. `policies/groot/` exists now, as do `pi0`, `pi05`,
   `smolvla`, `act`, `diffusion`, `vla_jepa` and about a dozen more. The table in
   `stack.md` is narrower than what is actually available.

Numbers worth having on hand. SmolVLA defaults to `chunk_size=50` and
`n_action_steps=50`, at the MuJoCo driver's 100 Hz that is exactly 0.5 s of intent,
matching the "0.5–1 s" in `docs/architecture.md`. It pads state and action to 32
dimensions (`max_state_dim`, `max_action_dim`), so a 5-joint arm is padded rather than
truncated; whatever converts `Action` into a policy tensor has to know that.

### MuJoCo, `3.12.0`, Apache-2.0

The simulator. Installs with pip, needs no GPU, and carries the contact physics the
manipulation literature trusts.

Three behaviours that cost time, all documented where they bite:

1. `meshdir` resolves against the top-level file, not the file that declared it. An
   included model's mesh paths break, and declaring `meshdir` before the `<include>`
   does not help, the include overwrites it. `<compiler>` must come after.
2. `<attach>` silently drops the attached model's `impratio` and `cone` in favour of the
   parent's defaults. Upstream sets `cone="elliptic" impratio="10"` for no-slip grasping,
   so an attached arm grips measurably worse, with only a stderr warning.
3. The XML parser opens files through a byte-oriented path API, so a **non-ASCII absolute
   path fails on Windows**, the file is readable, only MuJoCo cannot open it. 8.3 short
   paths do not help, since `GetShortPathNameW` only shortens components over eight
   characters. `drivers/mujoco.py` loads with the working directory moved to the scene
   folder instead, which makes every path relative and ASCII.

### DuckDB, `1.5.x`, MIT

The sidecar store: confidence traces, intervention flags, and later curation scores.
Embedded, zero-ops, reads parquet natively, which is what `LeRobotDataset` already
writes. `services/recorder.py` writes one transaction per episode, never per frame.

### Rerun, Apache-2.0/MIT, not yet wired

3D scenes and time-aligned sensor streams, with a web viewer the shell can embed. Worth
knowing that LeRobot already wraps it: `utils/rerun_visualization.py` exposes
`init_rerun()` and `log_rerun_data()` over the control loop, with a Foxglove backend
alongside. The shell should build on that rather than on bare `rerun-sdk`.

What Rerun does not do is exactly what the shell is for. It shows data; it has no notion
of pending intent, confidence, approval, or handover.

### PEFT, Apache-2.0, reached through LeRobot

LoRA. Not called directly: `PreTrainedPolicy.wrap_with_peft()` freezes the base model,
builds the config and returns the adapted policy, and `push_model_to_hub()` publishes it.
`docs/stack.md` describes composing PEFT + transformers + accelerate ourselves; most of
`services/trainer.py` turns out to be a call into this.

### Isaac Lab, rejected for v0.1–v0.3

Assessed against the `3.0.0-beta2` source in
[ADR 0002](../docs/decisions/0002-isaac-lab-is-a-later-driver.md). 441k LOC, 15 packages,
exact pins on Python and torch, RTX required, Linux-first. It becomes a driver when
vision-based Sim2Real or large-scale RL is needed, the concrete trigger being Newton
kit-less leaving beta.

---

## Adding to this directory

Read the porting rules in [`CONTRIBUTING.md`](../CONTRIBUTING.md) and copy
[`docs/provenance-template.md`](../docs/provenance-template.md). The two-minute version:

```
third_party/<project>/
    LICENSE          the original, unmodified
    PROVENANCE.md    source, commit hash, date, what was taken, what changed, why
    <files>          original copyright headers left intact
```

"Convenience" is not a reason to port. If it was convenience, add the dependency instead.
