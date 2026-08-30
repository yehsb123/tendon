"""A README that lists what is in a directory has to list all of it.

`services/README.md` described five modules. There were seventeen. Somebody opening it to
find out what the layer does would read a table covering a third of the directory and
reasonably assume that was the directory — including for four modules added in the last few
rounds, none of which appeared.

`kernel/README.md` opened with "the kernel owns four things and nothing else" and omitted
`types` and `protocols`, which are where `Action` and `Driver` live. `views/README.md`
listed four screens, said "the other three", and gave the job of showing the intervention
rate to `Training`, which does not exist — while `Progress`, which does, was not mentioned.

None of this breaks anything. It misinforms the next person to open the file, which is the
only reason the file is there.

## Why only some directories

`api/README.md` and `drivers/README.md` are prose about a boundary and do not enumerate
modules, which is a fine thing for a README to be. Requiring an index everywhere would
force one on documents that are better without. So the directories that *are* indexes are
named here, and the rule applies to them: adding a module to one fails this test until the
index mentions it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Directories whose README is an index of what is in them, as (source dir, README, suffix).
#:
#: Named explicitly rather than detected, because "does this README enumerate?" is a
#: judgement and a heuristic that guessed wrong would either nag about prose or quietly
#: stop checking an index.
INDEXED = (
    ("src/tendon/services", "src/tendon/services/README.md", ".py"),
    ("src/tendon/kernel", "src/tendon/kernel/README.md", ".py"),
    ("shell/src/views", "shell/src/views/README.md", ".tsx"),
)

#: A name in a table row or a fenced listing, which is how these indexes are written.
#: Matching the whole file would count invariants like "the kernel never imports `torch`"
#: as claims that a `torch` module exists — which is exactly what a first attempt did.
_TABLE_ROW = re.compile(r"^\|\s*`([A-Za-z_]+)`", re.MULTILINE)
_LISTING_ROW = re.compile(r"^([A-Za-z_]+)\s{2,}\S", re.MULTILINE)


def documented(readme: Path) -> set[str]:
    text = readme.read_text(encoding="utf-8")
    return set(_TABLE_ROW.findall(text)) | set(_LISTING_ROW.findall(text))


def present(directory: Path, suffix: str) -> set[str]:
    return {
        path.stem for path in directory.glob(f"*{suffix}") if path.stem not in {"__init__", "index"}
    }


@pytest.mark.parametrize(("source", "readme", "suffix"), INDEXED, ids=[i[0] for i in INDEXED])
def test_every_module_is_in_the_index(source: str, readme: str, suffix: str) -> None:
    missing = sorted(present(REPO / source, suffix) - documented(REPO / readme))

    assert not missing, (
        f"{readme} does not mention {missing}. A reader opening it to find out what "
        f"{source} contains would get an answer that is missing those."
    )


@pytest.mark.parametrize(("source", "readme", "suffix"), INDEXED, ids=[i[0] for i in INDEXED])
def test_the_index_does_not_name_things_that_are_gone(
    source: str, readme: str, suffix: str
) -> None:
    """The other direction, and the more misleading one.

    A missing entry is an omission. An entry for a module that was renamed or deleted sends
    somebody looking for a file that is not there, and reads as authoritative while doing
    it.

    An entry that says it is not built is not stale — it is the more useful thing to write.
    `views/README.md` lists `Training` and marks it, because a reader wondering where
    training went is better served by "not built" than by silence. So the rule is: a name
    with no file behind it has to say so on its own line.
    """
    text = (REPO / readme).read_text(encoding="utf-8")
    real = present(REPO / source, suffix)

    stale = []
    for name in sorted(documented(REPO / readme) - real):
        line = next((ln for ln in text.splitlines() if name in ln), "")
        if "not built" not in line and "stub" not in line:
            stale.append(name)

    assert not stale, (
        f"{readme} names {stale}, which are not in {source}. Either the file was renamed, "
        "or the entry should say it is not built."
    )


def test_the_parser_reads_the_indexes_it_is_pointed_at() -> None:
    """A pattern that matched nothing would make both tests above pass on empty sets —
    which is how a check that has quietly stopped working looks from the outside."""
    for _, readme, _ in INDEXED:
        assert len(documented(REPO / readme)) >= 4, readme


def test_it_does_not_read_invariants_as_module_names() -> None:
    """`kernel/README.md` says the kernel never imports `torch` or `mujoco`. Counting those
    as claimed modules is what the first version of this did, and it would have made the
    staleness test fail on a correct document."""
    named = documented(REPO / "src/tendon/kernel/README.md")

    assert "torch" not in named
    assert "mujoco" not in named
