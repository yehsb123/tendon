## What this changes

<!-- One paragraph. What is different afterwards. -->

## Which track

- [ ] **A** — integration: ported or adapted from open source
- [ ] **B** — structure: contracts, docs, tests, shell, scaffolding

See `docs/collaboration.md` for file ownership. A PR touching the other track column
needs a line in Status saying why.

## Design decisions touched

<!-- Which of the four this affects, and whether it still holds. Delete what does not apply. -->

- [ ] 1 — running is collecting
- [ ] 2 — intervention is an interrupt
- [ ] 3 — a body is a driver
- [ ] 4 — a skill is a package
- [ ] none

## If this ports code from elsewhere

- [ ] `third_party/<project>/LICENSE` present and unmodified
- [ ] `third_party/<project>/PROVENANCE.md` records repo, commit, date, what was taken, what changed
- [ ] original copyright headers intact
- [ ] a dependency was considered first, and why it was not enough is written down

## Checks

- [ ] `pytest tests/unit` passes
- [ ] boundaries still hold — the kernel imports no backend and no sibling layer
- [ ] works against the MuJoCo driver with no hardware attached
- [ ] documentation updated at the depth this change lives at
