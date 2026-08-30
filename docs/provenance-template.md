# PROVENANCE.md template

Copy this to `third_party/<project>/PROVENANCE.md` and fill it in. CI fails without it,
and the check is not bureaucracy: six months from now, nobody remembers which commit a
vendored file came from, and reconstructing it means diffing against a moving upstream.

Fill it at the time of the port. It takes two minutes then and an afternoon later.

---

```markdown
# Provenance — <project name>

**Source:** https://github.com/<owner>/<repo>
**Commit:** <full 40-character hash>
**Retrieved:** <YYYY-MM-DD>
**Licence:** <SPDX identifier, e.g. Apache-2.0 / BSD-3-Clause>

## What was taken

<Paths, relative to the upstream repository root. Be specific — a directory name is not
enough if you took part of it.>

- `path/in/upstream/` → `third_party/<project>/path/here/`

## What was changed

<Every modification, or "none". If nothing was changed, say so explicitly: a reader
otherwise has to diff against upstream to find out.>

- none

## Why this was ported rather than depended on

<The porting rule says prefer a dependency every time. This section is where you justify
not doing that. Acceptable reasons:

- the upstream package pulls in a stack we decided against (name it)
- the piece needed is a few hundred lines inside a framework we do not otherwise want
- it is data or assets, not code, and there is no package that ships them

"Convenience" is not a reason. If it was convenience, add the dependency instead.>

## Upstream licence obligations

<What the licence requires us to do, concretely. For Apache-2.0 and BSD-3 that is
attribution and keeping the licence text; note anything beyond that.>

- `LICENSE` copied unmodified alongside this file
- original copyright headers left intact in every file
- <anything else the licence asks for>

## Update policy

<How this gets refreshed, and what would make us refresh it. A vendored copy with no
update policy silently rots.>
```

---

## Notes on specific upstreams

**mujoco_menagerie** — each robot model directory carries its own `LICENSE`, separate
from the repository-level one, because models come from different authors under different
terms. Copy both: the top-level licence and the model's own. Record which model
directories were taken, since the repository holds dozens and a reader cannot tell from
the folder name alone which are present.

Menagerie models are assets rather than code, which is exactly the third acceptable reason
above — there is no package that ships MJCF and meshes for a specific arm, so vendoring is
the only option. Say that explicitly rather than leaving it inferred.

**LeRobot / MuJoCo / Rerun** — all pip-installable. Porting from these needs a strong
reason, because the dependency exists and is already in `pyproject.toml` as an extra.

**Isaac Lab** — BSD-3 core, but `isaaclab_mimic` depends on proprietary cuRobo. Check
which package a file came from before assuming the licence, and note it here.
