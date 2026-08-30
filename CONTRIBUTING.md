# Contributing

> **한글 요약.** 문서를 먼저 쓰고 코드를 씁니다. 물리량에는 반드시 단위를 적습니다.
> 커밋 메시지는 영어와 한글을 같이 씁니다. 의존성 추가는 보수적으로, 오픈소스에서
> 코드를 떼어올 때는 라이선스와 출처를 반드시 남깁니다.

## Getting started

```bash
git clone https://github.com/yehsb123/tendon.git
cd tendon
python -m pip install -e ".[dev]"
pytest tests/unit
```

No GPU, no robot, no simulator needed for that. If any of it requires more, that is a bug
worth reporting on its own.

Adding the simulator:

```bash
python -m pip install -e ".[sim,dev]"
```

## Two tracks

Work runs on two parallel tracks with separate file ownership. Read
[docs/collaboration.md](docs/collaboration.md) before touching anything — editing outside
your column loses someone else's work.

## Read first

- [docs/concepts.md](docs/concepts.md) — the four decisions everything descends from
- [docs/architecture.md](docs/architecture.md) — layers and the import rules
- [docs/stack.md](docs/stack.md) — what we depend on and why

A change that contradicts one of the four decisions is not automatically wrong, but it is
an ADR, not a patch. Open a design question first.

## Documents before code

This project is a set of claims about how physical AI should be operated. An idea that
cannot be written down clearly is not ready to be built, and code is the most expensive
place to discover that.

For anything that changes a contract or is hard to reverse, write the ADR first:
`docs/decisions/NNNN-short-slug.md`, following the shape of the existing two. Record the
alternatives you rejected. A decision with no stated alternative is a default nobody
examined.

## Code conventions

Some of these are borrowed from Isaac Lab `AGENTS.md`, which is unusually good on the
points that matter for physics code.

**Units are mandatory on every physical quantity.**

```python
joint_positions: Vector   # [rad], or [m] for prismatic joints
max_force: float          # [N]
horizon_s: float          # [s]
```

A number without a unit in a robotics codebase is a future incident. Where the unit
depends on joint type, say so explicitly rather than picking one.

**Name by prefix, for autocomplete.** `ActuatorNetLSTM`, not `LSTMActuatorNet`.
`set_joint_position_target()`, not `set_target_joint_position()`. Related things should
sort together when someone types the first few letters.

**Type hints use PEP 604.** `str | None`, never `Optional[str]`.

**Docstrings say why, not what.** The signature already says what. Use the docstring for
the constraint a reader cannot see: why the kernel must not import torch, why an
observation carries frames by reference, why a threshold is known to be wrong.

**Dependencies are added conservatively.** Solve it with what is already here — Pydantic,
numpy, the standard library — before adding anything. A dependency is added when it
removes more code than it adds, and questioned when it constrains one of the four things
tendon builds itself.

**Breaking changes deprecate first.** Keep the old name working for one minor version with
a warning that names the replacement.

## Porting code from open source

Prefer a dependency every time. Port only when the upstream package would drag in a stack
we have decided against, or when the piece needed is a few hundred lines inside a
framework we do not otherwise want.

When you do port, `third_party/<project>/` gets:

- the original `LICENSE`, unmodified
- `PROVENANCE.md` — source repo, commit hash, date, what was taken, what was changed
- original copyright headers left intact in every file

CI enforces the first two. The third is on you, and stripping a header is a licence
violation rather than a tidy-up.

## Tests

```
tests/unit/          pure logic, no simulator
tests/integration/   kernel plus a real driver, CPU only
```

Anything needing a GPU or physical hardware is marked `@pytest.mark.hardware` and skipped
by default.

**Curation metrics get the strictest tests.** A metric that mislabels good episodes as bad
poisons training silently, and nothing downstream catches it. Everything else fails loudly;
this fails quietly, so it carries the burden of proof.

**Regression tests must be seen to fail.** After writing one, revert the fix and confirm it
goes red. A regression test that passes against the bug is worse than none, because it
reports safety that does not exist.

## Before pushing

CI runs four jobs. Three of them can pass while the fourth fails, so run all of it:

```bash
ruff check src tests          # lint
ruff format src tests         # formatting — rewrites files, so run it before committing
pytest tests/unit             # 108 tests, no GPU, no simulator
```

`ruff format --check` fails CI on formatting alone, with every test green. That is the
job most likely to surprise you, because nothing is broken — the file is just shaped
differently than ruff would shape it.

Two lint rules bite most often here:

- **`zip()` needs an explicit `strict=`.** Choose it per call site rather than applying
  one everywhere. `strict=True` where a length mismatch would be a real bug — it turns a
  silent truncation into an exception. `strict=False` where the sequences differ by
  design, such as pairing a series with its own offset (`zip(xs, xs[1:], strict=False)`).
- **100 columns includes docstrings.** Markdown tables in a module docstring hit this
  constantly. Reflow them as prose rather than widening the limit.

## Commit messages

Write the subject in English, then the same line in Korean underneath, then a body that
carries both. Whoever reads this repository later should not need a translation to
understand why something changed.

```
feat: enforce safety limits on operator corrections
feat: 오퍼레이터 교정에도 안전 한계 적용

EN | Corrections arriving from the shell now pass through kernel/safety on the
same path as policy actions.

KO | 셸에서 들어온 교정도 정책 액션과 동일하게 kernel/safety를 거칩니다.

EN | A human may correct a policy but may not exceed a hard limit. Safety that
lives inside the thing being supervised is not safety.

KO | 사람이 정책을 교정할 수는 있어도 하드 리밋을 넘을 수는 없습니다. 감독 대상
안에 들어있는 안전장치는 안전장치가 아닙니다.
```

Do not add AI co-author trailers.

## Pull requests

Small and frequent. A branch that lives for days diverges invisibly, and this repository
has two tracks writing to it.

The template asks which of the four decisions your change touches. That is not paperwork:
if you cannot name one, the change may not belong in this project.
