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


#: Layers the architecture diagram names, as (heading, source dir, suffix).
#:
#: The diagram is the first thing anybody sees, and it had drifted further than any of the
#: module READMEs: five services out of seventeen, a `lerobot` driver that has never
#: existed, and "natural language correction" as a shell capability — a plan written into a
#: picture that reads as a description of what is there.
DIAGRAM_LAYERS = (
    ("SERVICES", "src/tendon/services", ".py"),
    ("KERNEL", "src/tendon/kernel", ".py"),
    ("DRIVERS", "src/tendon/drivers", ".py"),
)


def diagram_names(heading: str) -> set[str]:
    """Module names listed under one layer of the architecture diagram.

    Read between the layer's heading and the next `+---` rule, so a name in the prose below
    the diagram is not mistaken for a claim about a module.
    """
    text = (REPO / "docs/architecture.md").read_text(encoding="utf-8")
    start = text.index(f"|  {heading}")
    end = text.index("+---", start)

    # Digits included, or `so101` reads as `so`. The excluded words are the path fragment
    # each row ends with and the plain English in it — a row is a list of modules plus a
    # label, and only the first half is a claim about what exists.
    # `bus` is deliberately not excluded: the diagram writes "step bus" and the module is
    # `bus`, so excluding it as prose hid a real module from the completeness check.
    prose = {"src", "tendon", "py", "embodiment", "hal", "step", "correct", "a"}
    return {
        word
        for word in re.findall(r"[a-z_][a-z_0-9]*", text[start:end])
        if word not in prose and word not in {"services", "kernel", "drivers", "shell"}
    }


@pytest.mark.parametrize(
    ("heading", "source", "suffix"), DIAGRAM_LAYERS, ids=[layer[0] for layer in DIAGRAM_LAYERS]
)
def test_the_diagram_does_not_name_a_module_that_does_not_exist(
    heading: str, source: str, suffix: str
) -> None:
    """The direction that misleads. `lerobot` sat in the drivers row for months, and
    somebody looking for `drivers/lerobot.py` would have found nothing and assumed the
    install was broken."""
    named = diagram_names(heading)
    real = present(REPO / source, suffix)

    invented = sorted(n for n in named if n not in real and n not in {"base"})
    assert not invented, f"the {heading} row names {invented}, which are not modules in {source}"


@pytest.mark.parametrize(
    ("heading", "source", "suffix"), DIAGRAM_LAYERS, ids=[layer[0] for layer in DIAGRAM_LAYERS]
)
def test_the_diagram_names_every_module_in_the_layer(
    heading: str, source: str, suffix: str
) -> None:
    """The other direction, added after the first pass left `policy_lerobot` out.

    Only checking for invented names catches a diagram that lies and misses one that is
    merely out of date — which is how the SERVICES row came to list five modules out of
    seventeen without anything noticing.
    """
    missing = sorted(present(REPO / source, suffix) - diagram_names(heading) - {"base"})

    assert not missing, f"the {heading} row does not mention {missing}"


def test_the_diagram_reads_something() -> None:
    """A parser that matched nothing would make the check above pass on empty sets."""
    for heading, _, _ in DIAGRAM_LAYERS:
        assert len(diagram_names(heading)) >= 3, heading


def test_it_does_not_read_invariants_as_module_names() -> None:
    """`kernel/README.md` says the kernel never imports `torch` or `mujoco`. Counting those
    as claimed modules is what the first version of this did, and it would have made the
    staleness test fail on a correct document."""
    named = documented(REPO / "src/tendon/kernel/README.md")

    assert "torch" not in named
    assert "mujoco" not in named
