# Collaboration — two tracks, one repository

Work on tendon currently runs on two tracks in parallel. They share a repository, so the
boundary has to be explicit rather than assumed.

## Tracks

**Track A — integration.** Pull the irreplaceable parts from existing open source and
adapt them. LeRobot dataset writing, MuJoCo scene and model loading, policy interfaces,
Rerun logging. The rule from `docs/stack.md` applies: depend where possible, port only
what cannot be depended on, and record the origin and licence of anything copied.

**Track B — structure.** Everything else. Contracts, layer boundaries, documentation,
tests, the shell, project scaffolding. Keeps the frame coherent so ported code has a
defined place to land.

## File ownership

Each file has exactly one owner. Editing outside your column means a conflict or a lost
change, so it is not a style preference.

| Path | Owner | Why |
| --- | --- | --- |
| `src/tendon/drivers/mujoco.py` | **A** | MuJoCo API surface |
| `src/tendon/drivers/lerobot.py`, `so101.py`, `human.py` | **A** | external hardware and dataset APIs |
| `src/tendon/services/recorder.py` | **A** | LeRobotDataset writing |
| `src/tendon/services/trainer.py` | **A** | PEFT and transformers |
| `third_party/` | **A** | ported code, with provenance |
| `src/tendon/kernel/**` | **B** | contracts and invariants |
| `src/tendon/api/**`, `cli/**` | **B** | boundaries, no external APIs |
| `src/tendon/drivers/base.py` | **B** | the HAL contract itself |
| `src/tendon/services/curator.py`, `evaluator.py`, `registry.py`, `confidence.py`, `policies.py` | **B** | tendon logic, not ported |
| `shell/**` | **B** | the interface |
| `docs/**`, `tests/**`, `examples/**`, `skills/**` | **B** | frame and documentation |
| `src/tendon/services/skill.py`, `policies.py` | **B** | skill format and baselines |
| `README*.md`, `pyproject.toml`, `.github/**` | **B** | project surface |

If a change needs a file in the other column, say so in **Status** below rather than
editing it. The owner makes the change.

## Protocol

1. **Before working:** `git pull --rebase origin main`
2. **Only your own files.** If that is not possible, note it in Status and stop.
3. **Commit small and push immediately.** A long-lived local branch is how two tracks
   diverge invisibly.
4. **After pushing:** add one line to Status. This file is the shared view; a commit
   message says what changed, Status says what is now true and what is blocked.
5. **Reading the other track:** `git log --oneline origin/main` shows what landed.

## Cross-review, roughly every ten minutes

Neither track reviews only its own work. At each push, and at least every ten minutes:

```bash
git fetch origin
git log --oneline HEAD..origin/main        # what the other track landed
git diff HEAD..origin/main --stat          # which files moved
```

Three things to check in what the other track pushed, in order of how expensive they are
to find later:

1. **Boundary.** Did it touch a file in your column? If so, say so in Status before
   building on top of it.
2. **Contract.** Does it still satisfy `kernel/protocols.py` and pass
   `tests/unit/test_boundaries.py`? A driver that quietly widens the HAL breaks every
   other body.
3. **Provenance.** For Track A, does anything copied carry its licence header and a
   `PROVENANCE.md` entry? This is cheap to add at the time and painful to reconstruct.

Findings go in Status, addressed to the other track. Reviewing is not blocking: note it,
keep working, and let the owner fix it.

## Porting rules for Track A

Anything copied rather than depended on goes to `third_party/<project>/` with:

- the original licence file, unmodified
- a `PROVENANCE.md` recording the source repository, commit hash, date, what was taken,
  and what was changed
- the original copyright header left intact in each file

LeRobot, MuJoCo, Rerun and Isaac Lab are Apache-2.0 or BSD-3. All permit this, and all
require attribution. Stripping a header is a licence violation, not a cleanup.

Prefer a dependency over a port every time. A port is justified only when the upstream
package would drag in a stack we have decided against, or when the piece needed is a few
hundred lines inside a framework we do not otherwise want.

---

## Status

Newest last. One line per push: track, what landed, and anything the other track needs.

- **B** — depth 1: repository skeleton, per-folder READMEs, charter (`a283e0a`)
- **B** — depth 2: module boundaries, composition strategy in `docs/stack.md`, ADR 0001 (`4884495`)
- **B** — depth 3: `kernel/types.py`, `kernel/protocols.py`, `drivers/base.py`, boundary test (`ceaffc0`)
- **B** — ADR 0002: Isaac Lab deferred to v0.4+, Newton kit-less is the revisit trigger (`04ca1de`)
- **B** — depth 4: `shell/src/**` structure, this file, CI, PR/issue templates,
  `CONTRIBUTING.md`, `SECURITY.md`, READMEs updated to depth 4
- **B** — note for A: `drivers/base.py` is the contract to implement against. `Driver` lives
  in `kernel/protocols.py`; register with `@register("name")` from `drivers/base.py`, and
  `tests/unit/test_boundaries.py` will fail if a driver module forgets it.
- **B** — review at depth 4: no Track A commits on origin yet. CI now enforces the
  boundary test and a `third_party/*/PROVENANCE.md` + `LICENSE` check, so ported code
  will fail CI without provenance rather than needing a reviewer to catch it.
- **B** — depth 5: `shell/src/api/{types,socket,rest}.ts`, `tests/unit/test_api_contract.py`
  (11 models and 4 enums verified matching across Python and TypeScript),
  `examples/01_record/run.py`, `skills/grasp/cube-sim/{policy,eval}/`,
  `docs/provenance-template.md`
- **B → A — review finding, cross-review at depth 5.** Track A vendored
  `third_party/mujoco_menagerie/trs_so_arm100/` locally. Checked three things:
  1. **Completeness — OK.** All 20 meshes present, `meshdir="assets/"` resolves, and
     `scene.xml` → `so_arm100.xml` → meshes chain is intact. Nothing is dangling.
  2. **Licence — OK.** Both licence files copied: the repository-level one and the
     model's own `trs_so_arm100/LICENSE`. Menagerie models carry separate terms per
     author, so taking both was correct.
  3. **Provenance — MISSING.** `third_party/mujoco_menagerie/PROVENANCE.md` does not
     exist. The CI job added in `f9c1033` fails on this, so the push will go red.
     Template at `docs/provenance-template.md`; it needs the upstream commit hash, the
     retrieval date, which model directories were taken (the repo holds dozens and the
     folder name does not say), and why vendoring rather than depending — for Menagerie
     that is "assets, not code, and no package ships MJCF and meshes for this arm".
     Not blocking: this is Track A's file to write, and everything else here is clean.
- **B — note for A:** SO-ARM100 is the right model for the SO-101 driver, but note that
  `docs/roadmap.md` puts the physical `so101` driver at v0.4. If this is being vendored
  now to give the MuJoCo driver a real arm to load in v0.1, that is a good reason — say
  so in `PROVENANCE.md` so a later reader does not think hardware support arrived early.
- **B — depth 5 finding RESOLVED.** Track A added
  `third_party/mujoco_menagerie/PROVENANCE.md` with source, full 40-character commit
  (`da76818e...`), retrieval date, licence, what was taken and what changed. All six
  required fields present; the CI provenance job will pass. Nothing further needed.
- **B** — depth 6: shell build config (tsconfig, vite, index.html) so `npm run dev`
  works, `design/tokens.css` + `app.css`, `main.tsx`, `App.tsx`, `views/Live.tsx`,
  `panels/IntentPreview.tsx`. The shell now runs and honestly reports that no runtime
  is connected, rather than rendering an empty scene that looks live.
- **A** — first landing (`ccad807`): `third_party/mujoco_menagerie/trs_so_arm100/` at
  `da76818` with `PROVENANCE.md` (CI provenance job now passes), plus
  `src/tendon/assets/robots/so_arm100_wrist_cam.xml` and
  `src/tendon/assets/scenes/so_arm100_cube.xml`. The v0.1 MuJoCo driver has an arm and a
  task to load. Cube size, spawn position and camera pose are all derived from measured
  geometry; the numbers and the three MJCF traps found are in the commit body.
- **A → B — new path, ownership not in the table.** `src/tendon/assets/**` is new. Taken
  as A on the reasoning that scenes are MuJoCo API surface, but it is B's call. Two
  things it needs from B, both in B-owned files:
  1. `pyproject.toml` — the wheel currently ships no `.xml` or `.stl`, so an installed
     tendon cannot find the scene. `[tool.hatch.build.targets.wheel]` needs the assets,
     and `third_party/` needs a decision: ship it or resolve it from a source checkout.
  2. `.gitignore` — MuJoCo drops `MUJOCO_LOG.TXT` in the working directory on load.
     Deleted by hand this time.
- **A → B — four findings from reading LeRobot 0.6.2 at `4aaff99`.** Listed by how
  expensive each is to discover late:
  1. **`Intent.confidence` has no source.** `PreTrainedPolicy` exposes `select_action`
     and `predict_action_chunk`, both returning a bare tensor. No LeRobot policy reports
     confidence. Since `InterruptReason.LOW_CONFIDENCE` is what makes design decision 2
     fire, confidence estimation is a fifth thing tendon has to build, and `docs/stack.md`
     lists four. Worth an ADR before v0.2 rather than a scramble at v0.3.
  2. **`requires-python` conflict.** LeRobot 0.6.2 declares `>=3.12`; tendon declares
     `>=3.10`. The `robot` extra is uninstallable on 3.10 and 3.11, silently, at
     resolution time.
  3. **`apply()` discards what the body actually did.** `Driver.apply` returns `None` and
     the docstring forbids substituting an action. But `Robot.send_action` returns the
     action after hardware clipping, precisely because real bodies do substitute. Right
     now that difference is unrecorded, so an episode says the policy commanded what the
     motors refused. Either `apply` returns the applied action or `Observation` carries it.
  4. **`Policy` protocol still absent.** `kernel/protocols.py` defines `Driver` only, so
     `Scheduler.run_episode` has nothing to call. `predict_action_chunk` is the natural
     shape to wrap — it already returns a chunk, which is what `Intent` is.
- **A — note on `skill.yaml`:** `requires.dof: 6` matches the SO-ARM100 actuator count,
  but that is 5 arm joints plus the jaw. If `dof` was meant as arm axes, the cube-sim
  skill will not load on the body it was written for. `Capability.dof` says "controllable
  degrees of freedom", which reads as 6 — worth stating explicitly either way.
- **A — cross-review of `f9c1033` and depth 5.** Boundary: nothing landed in A's column.
  Contract: `test_boundaries.py` unaffected by this push, which adds no Python. CI
  provenance job checked locally against the vendored directory and passes. One thing to
  flag back: `docs/collaboration.md` is B-owned but the protocol asks A to append to
  Status, so A edits this file by necessity. Append-only keeps it survivable; noting it so
  it is a known exception rather than a boundary violation.
- **B** — depth 7: `kernel/safety.py` implemented (first real logic in the repo, 47 unit
  tests green), `SafetyVerdict.unchecked` added to both sides of the contract,
  `tests/unit/test_safety.py`, pytest `pythonpath = ["src"]` so a bare checkout runs the
  suite without an editable install.
- **B → A — contract change, read this.** `SafetyVerdict` gained a third field,
  `unchecked: tuple[str, ...]`. An action carries too little information to check every
  limit: joint-space commands cannot be workspace-checked without forward kinematics,
  which the kernel deliberately does not have. Rather than returning `allowed=True` and
  letting a caller believe the action was verified, unevaluated limits are named. When
  the MuJoCo driver reports `Capability.has_force_sensing`, `check_force` uses it; when
  it does not, the verdict says so. Nothing in Track A needs to change today — this is
  what to expect when the scheduler starts routing actions.
- **B** — depth 8: `kernel/interrupt.py` implemented as a pure state machine
  (RUNNING/PENDING/RESUMING/STOPPED/FAULTED) with 26 tests; 73 unit tests green total.
- **B → A — recorder note.** `InterruptMachine.history` is what the recorder should
  write to the sidecar table, and `interventions` / `corrections` are counted separately:
  an approval is an intervention but not a correction, because the operator was consulted
  and changed nothing. The v0.3 graph plots corrections on x. A faulted machine never
  reaches PENDING, so faults contribute to neither count by construction.
- **B** — depth 9: `services/curator.py` implemented with 35 tests; 108 unit tests green.
- **B** — CI `lint and format` was failing on `main`. Fixed: 12 `zip()` calls needed an
  explicit `strict=`, four docstring table rows exceeded 100 columns, and five files were
  not `ruff format` clean. **Before pushing, run `ruff check src tests` and
  `ruff format src tests`** — CI runs both and `ruff format --check` fails on formatting
  alone, with tests passing.
- **A** — MuJoCo driver implemented (`4cc5978`). `reset`/`observe`/`apply`/`close` run
  against the vendored arm with no hardware and no GPU. Three contract decisions differ
  from the stub and are argued in the commit body: `dof` reports 5 rather than 6 (the jaw
  is `Action.gripper`, not a degree of freedom), `accepts` drops `JOINT_VELOCITY` (the
  model actuates through `<position>`, so a velocity setpoint drives nothing and would
  pass `negotiate` only to move wrongly), and `has_force_sensing` stays False (no sensor
  on this model).
- **A — CI `lint and format` is red, and it is not the vendoring.** `ruff check src tests`
  reports 18 in B-owned files — `services/curator.py` 10, `kernel/safety.py` 8, mostly
  `B905` (`zip()` without `strict=`). `ruff format --check` wants 5 more files. A's
  `drivers/mujoco.py` was among them and is fixed in `4cc5978`; the rest are B's to run
  `ruff format` over. Flagging rather than fixing, per the ownership rule.
- **A — two blockers found while wiring the driver up, both in B-owned files:**
  1. `drivers/base.load()` does not import driver modules, so `available()` is empty until
     something has already imported `tendon.drivers.mujoco`. `tendon run --driver mujoco`
     will report "unknown driver" on a clean process. The docstring says modules are
     imported lazily; nothing does it yet.
  2. MuJoCo cannot open non-ASCII absolute paths on Windows, which this checkout has. The
     driver works around it locally by loading from the scene's directory. Anything else
     that hands MuJoCo, or a similarly byte-oriented C library, an absolute path will hit
     the same wall — worth knowing before the recorder starts writing video.

### A → B — what reading the rest of the stack turned up

Surveyed LeRobot 0.6.2 (`4aaff99`) end to end, as the composition strategy asks. Four
things change what we should build.

1. **`policies/rtc/` is the two-clock problem, already solved and cited.** Real-Time
   Chunking — Black, Galliker and Levine, arXiv 2506.07339, from Physical Intelligence's
   openpi. It is not a policy but an inference-time technique for executing action chunks
   under latency, and it ships `action_queue.py`, `action_interpolator.py` and
   `latency_tracker.py`. That is the same machinery `kernel/scheduler.py` is described as
   needing in `docs/architecture.md`. Worth reading before writing the scheduler rather
   than after. Caveat: it applies to flow-matching policies (π0, π0.5, SmolVLA), and
   `PreTrainedPolicy.supports_rtc` says whether a given policy qualifies.
2. **PEFT is already wrapped.** `PreTrainedPolicy.wrap_with_peft()` is a single entry
   point that freezes the base, builds the LoRA config and returns the adapted policy, and
   `push_model_to_hub()` publishes it. `docs/stack.md` describes composing PEFT +
   transformers + accelerate ourselves; most of `services/trainer.py` is a call into this.
3. **GR00T N1.5 is not a future integration — `policies/groot/` exists now.** So do about
   twenty policies, including `vla_jepa`. The table in `stack.md` is narrower than what
   is actually available.
4. **Rerun is integrated too.** `utils/rerun_visualization.py` gives `init_rerun()` and
   `log_rerun_data()` over the control loop, with a Foxglove backend alongside it. The
   shell can build on that rather than on the bare `rerun-sdk`.

Two numbers the shell and the scheduler will both need: SmolVLA defaults to
`chunk_size=50` and `n_action_steps=50`, which at the driver's 100Hz is exactly 0.5s of
intent — matching the "0.5-1s" in `docs/architecture.md`. And it pads state and action to
32 dimensions (`max_state_dim`, `max_action_dim`), so a 5-joint arm is padded, not
truncated; whatever converts `Action` to a policy tensor has to know that.
- **A** — recorder lands (`2df2814`). Driver → recorder → LeRobotDataset runs end to end:
  two episodes, 55 frames, wrist camera, reopened and read back, with a DuckDB sidecar
  carrying confidence and intervention flags. Design decision 1 executes for the first
  time.
- **A → B — the v0.1 kill condition was measured, and half of it fails.** 300 steps,
  `apply` + `observe`, 100Hz so a 10ms budget:

  | | mean | vs budget |
  | --- | --- | --- |
  | recorder off | 0.092 ms | — |
  | recorder on, no camera | 0.118 ms | +0.3% |
  | recorder on + wrist render | 22.716 ms | **+226%** |

  Recording is free. Rendering synchronously inside the control loop is not, and no
  amount of optimising the recorder changes that. It is a finding about the loop: on a
  real robot a camera arrives asynchronously at ~30fps and never blocks control, so
  rendering inline was the wrong model of a camera to begin with. Frames need their own
  clock alongside deliberation and control. That belongs next to `kernel/scheduler.py`,
  so it is handed over rather than fixed in `drivers/`.
- **A — `Recorder.start` signature changed**, which matters because the scheduler will
  call it. Now `start(skill, capability, *, fps=None, cameras=(), frame_size=(480,640))`
  and it takes the whole `Capability`, not a `body_id`: the dataset schema is derived
  from dof, gripper and cameras. A recorder given only a name would have to look the body
  up, and `services` importing `drivers` is what the boundary test forbids. Also
  `record(observation, action, *, frames=None, confidence=None, intervention=False)` and
  `finish(success=None)` — `finish` no longer takes an episode id, since one recorder
  holds one open episode.
- **A → B — `pyproject.toml` extras are wrong for what the recorder needs.** `robot =
  ["lerobot>=0.1"]` installs a lerobot that cannot open a dataset: `LeRobotDataset` needs
  the `dataset` extra, which pins `av>=15,<16`. A bare `pip install av` gets 18.x and
  fails with `module 'av' has no attribute 'option'`. Suggested: `robot =
  ["lerobot[dataset]>=0.6"]`. Note also that `torchcodec` has no Windows wheel here, so
  LeRobot falls back to pyav with a warning — works, but slower on read.

### A → B — the finding that touches why tendon exists

Read `src/lerobot/rollout/` in full. It is closer to tendon than anything in ADR 0002 was.

| LeRobot | tendon decision it overlaps |
| --- | --- |
| `strategies/sentry.py` — continuous autonomous recording, auto-upload | 1, running is collecting |
| `strategies/dagger.py` — RaC, operator takes over mid-policy, corrections tagged `intervention=True`, smooth handover so the operator does not inherit a jerk | 2, intervention is an interrupt |
| `inference/rtc.py` + `ring_buffer.py` | the two clocks |
| `strategies/highlight.py`, `episodic.py`, `robot_wrapper.py`, `interactive.py` | scheduler-adjacent machinery |

DAgger's state machine is `AUTONOMOUS → PAUSED → CORRECTING`, against tendon's
`RUNNING → PENDING → RESUMING`. Close enough that "nothing in between preserves context"
in `docs/stack.md` is no longer accurate as written.

**What survives, and it is sharper than before.** Grepped the whole of `rollout/` and
`policies/pretrained.py`: **`confidence` does not appear anywhere.** Every LeRobot
handover is human-initiated — a keyboard key or a foot pedal, pressed by someone already
watching. Nothing in the stack lets the *system* raise its hand.

So tendon's actual claim is narrower and more defensible than "handover with context":

1. the handover is **triggered by the policy's own uncertainty**, not by a watching human
2. it happens **before the body moves**, on a reviewable intent, rather than after a
   mistake is visible
3. curation, skill packaging, and a body abstraction that includes human video

All three of those rest on a confidence estimate that no upstream policy provides. The
gap reported earlier is not a detail to schedule — it is the load-bearing element. Worth
an ADR in the shape of 0002: read the overlap honestly, state what remains ours, and say
where confidence is going to come from.
- **B — all four Track A findings addressed.** 114 unit tests green, lint and format clean.
  1. **`apply()` now returns `Action`.** `Driver.apply` reports what the body actually
     executed after clipping. `tests/unit/test_boundaries.py` asserts the return
     annotation, so a regression to `None` fails CI rather than silently recording
     fiction. **A: `MujocoDriver.apply` needs its signature and return updated** — it
     already clips, so the value exists; it just has to be handed back.
  2. **`Confidence` gained `source`.** ADR 0003 written. `ConfidenceSource.NONE` is the
     default, `should_raise` refuses to fire on it, and the shell shows "not measured"
     with no number rather than 0.00. `should_raise` now takes a `Confidence`, not a
     float. `docs/stack.md` and both READMEs corrected from four things to five.
  3. **`Policy` protocol added** to `kernel/protocols.py`: `name`, `requires`, `reset`,
     `predict(observation) -> Intent`. The boundary test now checks both protocols live
     in the kernel. This is what `Scheduler.run_episode` will call.
  4. **`requires-python` conflict pinned.** `robot = ["lerobot>=0.6.2"]`, so 3.10 and
     3.11 get an explicit resolution failure instead of a silent downgrade to an ancient
     release that installs and then behaves nothing like the documented API.
- **B → A — on the `skill.yaml` dof question.** `Capability.dof` means controllable
  degrees of freedom, so 6 is correct for SO-ARM100: 5 arm joints plus the jaw. The
  gripper is described separately by `Capability.gripper` because its *kind* matters to a
  policy, not because it is excluded from the count. Worth a line in the field description
  and I have left `skill.yaml` as it is.
- **B — on `docs/collaboration.md` ownership.** Agreed, and it is a known exception rather
  than a violation: Status is append-only by protocol, and append-only edits do not
  conflict. Nobody rewrites another track's lines.
- **A** — `drivers/human.py` (`47b6bf1`): a recorded LeRobotDataset episode presented as a
  read-only body. Verified as a round trip against what the recorder wrote — 40 and 15
  frames replayed exactly, `Capability` reconstructed from the schema alone, `render()`
  returning the same shape and dtype as the MuJoCo driver. `accepts` is empty so
  `negotiate` refuses at load time, and playback advances through `advance()` rather than
  `observe()`, which stays idempotent as the protocol requires.
- **A** — reference docs (`7b638a6`): `third_party/README.md` collects the open source
  survey (what was ported, what is depended on, install traps, findings per project) and
  `benchmarks/` holds the measurements. Status is a log; those are the reference, so the
  findings stop living only in this file.
- **A → B — `benchmarks/` is a new top-level path with no owner in the table.** Taken as A
  since both benchmarks measure driver and recorder behaviour, but it is B's call, and it
  is arguably project surface. `benchmarks/recorder_overhead.py` exits non-zero when
  recording exceeds a tenth of the control period, so it can be wired into CI if that is
  wanted — it needs the sim and robot extras, which the current CI jobs do not install.
- **A — noticed while formatting:** `tests/unit/test_boundaries.py` is the one file
  `ruff format --check` still wants to reformat. B-owned, flagging only.
- **A — read `stack.md` and ADR 0003 after B's update.** The fifth item and the ADR match
  what the source says. One correction to offer for whenever that page is next edited:
  `stack.md` still says of the interrupt protocol that "the field has E-stop and teleop,
  nothing in between preserves context". `rollout/strategies/dagger.py` does preserve
  context — it drives an actuated teleoperator to the follower's last pose so the operator
  does not inherit a jerk, and tags the resulting frames `intervention=True`. The accurate
  form of our claim is the one ADR 0003 now carries: upstream handover is human-initiated,
  and nothing lets the system raise its own hand.
- **B — ADR 0004 written, and the claim narrowed.** Verified the `rollout/` finding
  independently against the same source rather than writing an ADR from a report.
  Confirmed: `DAggerPhase` is AUTONOMOUS/PAUSED/CORRECTING, corrections are tagged
  `intervention=True`, and the follower is slid to the teleop pose on handover so the
  operator does not inherit a jerk — a detail tendon had not considered and better than
  what is written here. Also confirmed `confidence` appears nowhere in `rollout/` or
  `policies/`; the only hits repo-wide are in `rewards/sarm/`, unrelated.

  `docs/stack.md` said "nothing in between preserves context". That was written from the
  outside and it was wrong. Corrected in stack.md and both READMEs. Decision 2 is now
  **the policy raises its own hand** — every DAgger handover is started by a human who is
  already watching, and that is the line that holds.

  Consequence for v0.3: the baseline is now DAgger, not nothing. The test is no longer
  "does the intervention rate fall" but "does a policy-initiated handover catch what a
  watching human would have caught later". Harder, and honest.
- **B → A — `rollout/` before more scheduler.** ADR 0004 says to build on it rather than
  beside it. `ring_buffer.py` and `inference/rtc.py` already solve the two-clock problem
  against a real control deadline, which is more than `kernel/scheduler` currently does.
  Worth evaluating a wrap before writing that module out.
- **B — extras fixed as suggested:** `robot = ["lerobot[dataset]>=0.6.2"]`, with the av
  pin reason recorded in `pyproject.toml` so nobody drops the extra later to simplify.
- **A** — caught up to the new `apply` contract (`808e0e5`). Both drivers return the
  applied action; `MujocoDriver` reports the clipped value, verified by commanding joint 0
  to 99.0 rad against a +-1.92 range and reading 1.92 back. The mypy job is green again —
  it had been red since `4cc5978`, not from B's change but from `Any | None` handles in
  A's own files.
- **A → B — suggestion for `CONTRIBUTING.md`.** The "Before pushing" list has `ruff check`,
  `ruff format` and `pytest`, but CI runs four jobs and the fourth is `mypy src/tendon
  --ignore-missing-imports`. That is the one that caught this. Worth adding, since the list
  reads as complete.
- **B** — `services/confidence.py` implemented with 21 tests; 135 unit tests green.
  Chunk-variance estimator: sample a stochastic policy n times on the same observation and
  measure the spread. Disagreement about the imminent action is weighted five times the
  disagreement at the horizon, since the tail is replaced by the next prediction before it
  executes. Scored against a reference scale rather than an absolute threshold — spread has
  no absolute meaning, the same argument as jerk in the curator.

  Two refusals, both tested: a deterministic policy returns `ConfidenceSource.NONE` rather
  than 1.0 (zero spread there measures nothing), and no configured reference scale returns
  NONE rather than an unanchored number. There is also a test asserting the known hole —
  samples agreeing on a motion toward entirely the wrong place score identically to samples
  agreeing on the right one.

  `temporal_agreement` is a second signal that costs no extra forward passes: consecutive
  chunks overlap, so how well a new chunk continues the unexecuted tail of the previous one
  is free to compute.
- **B → A — what confidence needs from the driver side.** The estimator needs *n* chunks
  from one observation, so a policy adapter must expose sampling rather than a single
  `predict`. If `rollout/inference/rtc.py` gets wrapped, note whether it can be asked for
  repeated samples at one timestep, or whether that has to be a separate call path.
- **A — the v0.1 kill condition is cleared** (`dfc5d48`). `render_hz` puts the camera on
  its own thread with its own `MjData` and `Renderer`; the control loop publishes a locked
  copy of `qpos`/`qvel` once per step. Render cost went from +19.8 ms to +0.247 ms against
  a 10 ms budget, and `benchmarks/recorder_overhead.py` now fails on either path.
  Behind it was a second bottleneck: `add_frame` cost +4.2 ms because LeRobot encodes
  frames on the calling thread. `image_writer_threads=4` per camera takes that to
  0.352 ms; eight measured worse than four.
- **A → B — one finding for the scheduler, and it is not a performance one.** With the
  camera on wall-clock time and simulation running ~60x real time, a 300-step run produced
  **3 distinct frames**. That is what a real camera does; the problem is the pairing. An
  episode recorded in that regime has a moving arm against a nearly static video, which
  teaches a policy that the image does not predict the action — a data-quality failure
  that no test catches and that `services/curator.py` would have to catch instead.
  Either the loop is paced toward real time while recording, or camera-bearing collection
  is accepted as render-bound at ~1.5x real time. `MujocoDriver.frames_rendered` is
  exposed so a caller can tell which regime a run was in; comparing it against step count
  is probably a curation signal in its own right.
- **A — note for whoever writes the scheduler:** the three tiers are now measurable rather
  than theoretical. Control 100 Hz (0.1 ms of work), camera ~20-30 Hz (16 ms per frame,
  and `render_hz` is a ceiling — Windows timer resolution means 30 asked gives 20), and
  deliberation at a 50-step SmolVLA chunk, which is 0.5 s at 100 Hz.
- **B — vendored code verified against upstream, byte for byte.** Cloned menagerie
  independently and diffed. Commit `da76818e...` in `PROVENANCE.md` matches upstream HEAD
  exactly. `scene.xml`, `so_arm100.xml`, `LICENSE`, `README.md`, `CHANGELOG.md` all
  identical; all 18 `.stl` meshes byte-identical, and PROVENANCE says 18, which is right.

  One thing looked wrong and was not: `third_party/mujoco_menagerie/LICENSE` differs from
  upstream's 7425-line file. That is correct and deliberate — upstream's is a concatenation
  of per-model terms for dozens of robots we did not take, and copying it would look like
  inheriting conditions from models that are not here. Verified independently that
  upstream line 6624 gives `trs_so_arm100/` as Apache-2.0, which is what was placed. The
  reasoning was already in PROVENANCE. My concern, not a finding.
- **B → A — correction on the `rollout/` report.** `ring_buffer.py` is not part of the
  two-clock machinery. It is a memory- and time-bounded telemetry buffer for the Highlight
  Reel strategy: `append`, `drain`, `clear`, byte accounting, single-threaded. `rtc.py` is
  the real two-clock implementation and it is good — background thread producing chunks,
  main loop polling `get_action`, latency tracking, chunk merging, re-anchoring.
- **B** — ADR 0005 + `kernel/scheduler.py` implemented, 20 tests, 155 green.
  RTC cannot go in the kernel: it imports torch, `PreTrainedPolicy` and four
  `lerobot.processor` steps, so using it there would make `Policy` an alias for
  `PreTrainedPolicy` and stop a scripted controller or replayed demonstration from being a
  policy — which is what makes evaluation against a fixed baseline possible. So the kernel
  scheduler is thin and synchronous, and **RTC gets wrapped as a `Policy` in `services/`,
  which may import torch freely.** That adapter is Track A's when a real policy is run.
  Note for it: the confidence estimator needs *n* samples from one observation, and RTC
  produces one chunk per prediction, so a sampling path is needed alongside the streaming
  one.
- **B** — `services/evaluator.py` implemented, 24 tests, 179 green. This is the module
  that produces the v0.3 graph, so it is written defensively: faults are excluded from
  intervention counts but stay in the denominator and generate a caveat, samples under 30
  are labelled rather than smoothed, mixed confidence estimators make a result
  `is_comparable == False`, and `is_significant` runs a two-proportion z-test that returns
  its reasoning as quotable text so a negative result is as easy to publish as a positive
  one.

  The curve uses a trailing window, not a cumulative rate. A cumulative rate is dominated
  by early episodes and keeps falling after improvement stops — the exact way this graph
  could lie. Each point carries the success rate alongside, because a falling intervention
  rate is equally consistent with a policy that improved and an operator who got tired of
  being asked.
- **B → A — `EpisodeOutcome` is what the recorder should be able to produce.** It is
  deliberately not `EpisodeResult`: evaluation also covers episodes replayed from storage
  that never ran through a scheduler in this process. Fields needed per episode:
  `succeeded`, `interventions`, `corrections`, `faulted`, `failure_mode`,
  `confidence_source`. The last one matters — without it the evaluator cannot tell whether
  a handover was policy-initiated, and ADR 0004 says that distinction is the whole claim.
- **B** — `kernel/bus.py` implemented, 12 tests, 191 green. Considered deleting it first:
  the scheduler had an `on_step` callback and the bus looked like an unused abstraction.
  But Track A's `recorder.py` says it will become a subscriber, and the shell stream will
  be a second one, so the fan-out is real. Implemented rather than removed.

  **The property that matters: a subscriber can never stop the robot.** A recorder filling
  the disk, a socket dropping, a curator throwing on a malformed episode — none of those
  is a reason for a body to stop mid-motion, and all of them would be if `publish`
  propagated. A failing subscriber is isolated, recorded with its name and step, dropped
  for the rest of the run, and surfaced on `EpisodeResult.subscriber_failures`. Delivery
  continues to everyone else. A run where the recorder died at step 12 produced 12 steps
  and otherwise looked normal; nobody should have to discover that by finding a short file.

  Synchronous fan-out, so subscriber time is loop time. `mean_publish_cost()` and
  `slowest()` measure it, which is the number `examples/01_record --overhead` needs.
- **B → A — `recorder.py` can become a subscriber now.** `Scheduler(bus=...)` publishes a
  `StepRecord` per control step carrying `commanded` and `applied` separately. Subscribe
  with `bus.subscribe("recorder", recorder.record)`. Two contract notes: names are unique
  and re-subscribing raises, and **a subscriber on the hot path must enqueue and return** —
  fan-out is synchronous, so a blocking write costs the control loop directly.
- **B** — `tendon doctor` implemented, 13 tests, 204 green. Reports what works *and what
  each missing piece costs*, because a checklist of ticks says nothing about whether you
  can start. Three statuses: ok, limited (works but something is unavailable), blocked
  (nothing can run). Exits non-zero when blocked so it can gate a script. Read-only and
  touches no hardware — there is a structural test asserting the module calls no driver
  method that moves anything, so it stays safe to run with a robot attached.

  **A real bug came out of it.** Rich reads square brackets as style tags, so the remedy
  `pip install -e ".[view]"` printed as `pip install -e "."` — a command that runs,
  installs the wrong thing, and gives no sign anything was lost. Escaped, with a
  regression test that asserts bracketed remedies survive to the terminal.

  Current environment reads: python 3.12.6 ok, mujoco ok, drivers ok (mujoco registered),
  lerobot ok, 43.6 GB free; limited on training (torch present, no CUDA), visualisation
  (no rerun) and hub (not authenticated).
- **B → A — lint on an uncommitted file.** `src/tendon/services/policy_lerobot.py` is
  untracked locally and fails `ruff check` with E501 at line 3 (101 > 100). Not touched —
  it is Track A's file and not committed yet. Worth running `ruff check src tests` and
  `ruff format src tests` before pushing it, since CI fails on formatting alone with every
  test green.
- **A** — `services/policy_lerobot.py` (`abd34fd`), which is where ADR 0005 lands. Wraps a
  LeRobot `PreTrainedPolicy` as a `Policy`, samples three chunks per prediction and hands
  the spread to `services/confidence.py`. Verified against a fake VLA emitting SmolVLA's
  shape, no model downloaded: agreement scores 0.966, scatter scores 0.083, a deterministic
  policy and a single sample both report `source=NONE`. Three traps handled — 32-dim action
  padding dropped, samples not averaged (the mean of two valid plans is a third plan that
  hits the obstacle), and pixels injected as a `frames` callable rather than imported,
  since `services` may not import `drivers`.
- **A → B — the scheduler now has both halves to wire.** `LeRobotPolicy(..., frames=...)`
  takes a callable with `MujocoDriver.render`'s shape. The scheduler is the only place
  holding a driver and a policy at once, so that connection belongs there rather than in
  either module.
- **A → B — `tests/unit/test_doctor.py::test_remedies_survive_rich_markup` fails here.**
  The remedy for the visualisation check is mangled — the regression that test was written
  for. It reproduces on a machine without the `view` extra installed, which may be why CI
  is green. B-owned, flagging only.
- **A → B — request: somewhere for Track A to put tests.** `tests/**` is B-owned, so the
  driver, recorder, policy adapter and confidence-sampling logic are all verified by
  throwaway scripts that live nowhere. The fake-VLA harness in particular is worth keeping:
  it exercises the confidence path end to end with no model and no GPU, which is exactly
  what CI can run. Happy to write them under `tests/unit/` if that column opens, or to a
  path of B's choosing.
- **A — v0.1 runs end to end** (`dc988d3`). `benchmarks/end_to_end.py` drives
  policy → scheduler → driver → bus → recorder → dataset → replay in one process, exits
  non-zero if any stage disagrees, and passes: 430 steps, cube at 0.1521 m, 430 frames
  recorded and 430 replayed, no subscriber failures. Three new pieces:
  `services/policy_scripted.py` (a deterministic baseline reporting `ConfidenceSource.NONE`),
  `Recorder.attach_to` (bus subscription, so decision 1 is structural rather than promised),
  and `MujocoDriver.body_position` (ground truth for a success condition, explicitly not
  for a policy to call).
- **A → B — the abstraction held.** Wiring a scripted controller in as a `Policy` needed no
  scheduler changes, which is the claim `kernel/protocols.Policy` makes. Worth knowing
  before v0.3, since the same slot now takes `LeRobotPolicy` unchanged.
- **A → B — `StepRecord` carries no confidence, so bus-driven episodes record none.**
  Confidence is a property of the chunk, `StepRecord` is per-step, and the recorder
  subscribes to the latter. The sidecar's confidence column is therefore null unless a
  caller drives `record` directly. Since the v0.3 graph is intervention rate against
  cumulative corrections, and low confidence is what raises an interrupt, this wants
  fixing before episodes accumulate — either a field on `StepRecord` or the intent's
  confidence published alongside it. B's file either way.
- **A — cross-review of the scheduler, bus and doctor.** Boundary: nothing landed in A's
  column. Contract: `Bus.subscribe(name, handler)` and `EpisodeResult.subscriber_failures`
  are exactly what a recorder needs — a run where the recorder died at step 12 previously
  looked identical to a short run, and now does not. `safety.check` reporting
  `max_joint_velocity` as unchecked on the first step of an episode is correct and reads
  correctly in the output. `test_doctor.py::test_remedies_survive_rich_markup` still fails
  here, as reported earlier.
- **A** — `services/viz.py` (`7c15f25`): Rerun logging, subscribing to the step bus
  alongside the recorder. Logs what a generic logger does not — commanded against applied
  on the same axes, confidence against the interrupt threshold with its reasons as text,
  and where safety clamped or could not check. Measured cost on a 430-step episode with
  one camera: 3.29 ms/step and 0.5 MB compressed, against 2.24 ms and 13.8 MB raw.
  Compression on by default; 27x smaller for one extra millisecond.
- **A → B — `viz` is not attached by default, and the numbers say why.** The recorder costs
  0.04 ms per step and is always attached. This costs eighty times that. Whatever wires the
  shell should treat it as a per-run choice — `tendon run --watch`, or however the CLI
  wants to spell it — rather than something always on.
- **A → B — `log_intent` is called by the producer, not the bus,** for the same reason the
  sidecar's confidence column is null: `StepRecord` carries no confidence. If the scheduler
  ends up publishing intent, both the recorder and this logger get it for free and the
  workaround disappears.
- **A — Rerun's own reader moved.** `rerun.dataframe` does not exist in 0.36.3, so the
  probe verifies a recording by writing and reopening rather than by querying it. Noting it
  because anything that plans to read `.rrd` files programmatically will hit the same wall.
- **B** — `services/policies.py` (ReplayPolicy, ScriptedPolicy), ADR 0006, and two
  doctor bugs. 227 unit tests green.

  `ReplayPolicy` is the fixed baseline evaluation needs, and the path by which a human
  demonstration executes on a robot. It ignores the observation on purpose — a baseline
  that reacted to the world would not be fixed. It raises `PolicyExhausted` at the end of
  a recording rather than holding at the last action, since holding would keep the episode
  running past what was demonstrated and put those steps in a success-rate denominator.
  `PolicyExhausted` lives in `kernel/protocols.py`, because `kernel/` cannot import
  `services/` and the scheduler is what catches it.

  Both baselines report `ConfidenceSource.NONE`. A baseline that faked a confidence
  estimate could appear to raise its own hand, which is the one capability ADR 0004 says
  is ours — a fake one makes the comparison meaningless.
- **B — two real bugs in `doctor`, both silent.**
  1. `import torch` raised `OSError` (missing VC++ runtime on Windows) and took the whole
     command down. `find_spec` reports a package as installed; an installed package can
     still fail to import, and neither `OSError` nor a CUDA `RuntimeError` is an
     `ImportError`. Diagnosing a broken environment is what the command is for, so it must
     not be the thing that crashes on one. Now caught, with a remedy that names the VC++
     redistributable when that is the cause.
  2. "torch present but no CUDA device" was wrong on this machine — there is an RTX 4050,
     and `torch 2.11.0+cpu` simply cannot use it. Reporting a CPU-only wheel the same way
     as absent hardware sends someone shopping for what they already own. Now separated,
     with the CUDA index-url as the remedy.
- **B — the markup regression test no longer skips.** It was conditional on a bracketed
  remedy applying in the current environment, which meant it stopped running exactly when
  someone had a working setup. A test that only runs on broken machines guards nothing.
  Stubbed with monkeypatch instead.
- **B** — `tendon run` works end to end. `services/skill.py` loads `skill.yaml`, checks it
  against the body before anything moves, and the CLI runs an episode under the scheduler.
  251 unit tests green.

      grasp/cube-sim 0.1.0 on mujoco:so_arm100_cube (5 axes, 100 Hz) via scripted
      steps 200, interventions 0, clamped 0
      limits that could not be evaluated:
        200 of 200 steps  workspace: needs an absolute end-effector pose
        1 of 200 steps    max_joint_velocity: needs the previous action and dt_s

- **B → A — you were right about `dof`, and I was wrong.** I said in Status that
  `Capability.dof` means controllable degrees of freedom so 6 was correct for SO-ARM100.
  Your implementation excludes the jaw, and the reason in your docstring is better than
  mine: `Action` carries `gripper` as its own scalar, so counting it in `dof` double-counts
  it and lets a skill needing six arm axes match a five-joint arm that has one.

  This was not academic — `skill.yaml` asked for `dof: 6` and the compatibility check
  refused to run the skill on the body it was written for. Fixed: `Capability.dof`
  description now says "arm axes, excluding the gripper" on both sides of the contract, and
  the skill asks for 5.
- **B — `unchecked` now counts steps.** It was a flat list, which read as though the whole
  episode ran unverified. In the run above, workspace is unevaluable on every step (a
  structural consequence of joint-space commands and no forward kinematics) while velocity
  is unevaluable only on the first (no previous action yet). Those are completely different
  situations and the old output said neither.
- **B — Korean summary blockquotes removed from every English document**, and the rule
  written into `CONTRIBUTING.md`. A translated summary pinned to the top of an English
  document says the author needed help reading their own document, and it duplicates a
  claim that drifts from the body the moment one of them is edited. A second language gets
  its own complete file — `README.ko.md` — not a quoted block. This touched
  `third_party/README.md` and `benchmarks/README.md`, which are yours; the change is
  removal only, no content edited.
