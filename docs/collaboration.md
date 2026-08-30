# Collaboration — two tracks, one repository

> **한글 요약.** 지금 두 세션이 동시에 작업 중입니다. 같은 파일을 건드리면 서로 덮어쓰니
> 담당 영역을 아래 표로 나눴습니다. 커밋 전에 반드시 `git pull --rebase`, 자기 영역만
> 수정, 그리고 아래 **Status** 섹션에 뭘 했는지 한 줄 남기기. 그러면 상대가 pull만 해도
> 진행 상황을 압니다.

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
| `src/tendon/services/curator.py`, `evaluator.py`, `registry.py` | **B** | tendon logic, not ported |
| `shell/**` | **B** | the interface |
| `docs/**`, `tests/**`, `examples/**`, `skills/**` | **B** | frame and documentation |
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
