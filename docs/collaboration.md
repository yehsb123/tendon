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
