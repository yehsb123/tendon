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
