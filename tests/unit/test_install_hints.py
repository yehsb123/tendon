"""Every `pip install` this project tells someone to type has to work.

Written after `examples/01_record/run.py` was given a refusal path that said

    Install the recording extra:  pip install -e ".[record]"

There is no `record` extra. It is called `robot`. The hint would have been printed at
exactly the moment somebody was already stuck — the recorder is missing, that is why they
are reading it — and it would have sent them to a resolution error.

That is the same shape as the bugs this project keeps finding: something that looks
available and is not. A refusal that hands over a command which does not work is worse
than a refusal that hands over nothing, because the reader spends their time believing
they have been helped.

## Why a test rather than care

Extras get renamed. `robot` was very nearly `record`, and the name that ended up in
`pyproject.toml` is not the name the feature is called in prose anywhere else in the
repository. Nothing else notices: these strings live in docstrings, error paths and
markdown, none of which are executed by anything.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: `pip install -e ".[sim,dev]"` and every spelling around it. The extras are the capture.
INSTALL_HINT = re.compile(r"pip install[^\n]*?\.\[([A-Za-z0-9_,\- ]+)\]")

#: Where a user-facing install hint can live. Deliberately includes docstrings and
#: markdown: that is where all of them actually are.
SEARCHED = ("*.py", "*.md")

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".ruff_cache"}


def declared_extras() -> set[str]:
    """Extras named in `pyproject.toml`.

    Parsed with a regex rather than `tomllib`, which is 3.11+ while this project supports
    3.10. Reading the section directly also keeps the test honest about what it checks —
    it fails if the section is renamed, rather than quietly checking an empty set.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")

    start = text.index("[project.optional-dependencies]")
    rest = text[start + len("[project.optional-dependencies]") :]
    end = rest.find("\n[")
    section = rest if end == -1 else rest[:end]

    found = set(re.findall(r"^([A-Za-z0-9_\-]+)\s*=", section, flags=re.MULTILINE))
    assert found, "no extras parsed from pyproject.toml — the section shape changed"
    return found


def searched_files() -> list[Path]:
    """Every file that could carry an install hint, except this one.

    This file is excluded because it deliberately contains a broken hint — the one that
    prompted it, quoted in the module docstring and asserted against below. Excluding the
    whole of `tests/` instead would be the easier fix and the wrong one: a test that tells
    a contributor to install a nonexistent extra is exactly as misleading as anywhere else.
    """
    files: list[Path] = []
    for pattern in SEARCHED:
        for path in REPO.rglob(pattern):
            if SKIP_DIRS & set(path.parts):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            files.append(path)
    return files


def test_every_install_hint_names_a_real_extra() -> None:
    wrong: list[str] = []
    declared = declared_extras()

    for path in searched_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for match in INSTALL_HINT.finditer(text):
            line = text[: match.start()].count("\n") + 1
            for extra in (e.strip() for e in match.group(1).split(",")):
                if extra and extra not in declared:
                    wrong.append(
                        f"{path.relative_to(REPO)}:{line} suggests .[{extra}], "
                        f"which is not an extra. Declared: {sorted(declared)}"
                    )

    assert not wrong, "install hints that would fail:\n  " + "\n  ".join(wrong)


def test_the_scan_finds_the_hints_that_are_there() -> None:
    """A pattern that matched nothing would pass the test above on an empty set.

    The README documents the simulator install, so at minimum that one has to be found.
    """
    hits = [
        m.group(1)
        for path in searched_files()
        for m in INSTALL_HINT.finditer(path.read_text(encoding="utf-8", errors="ignore"))
    ]

    assert len(hits) > 3, f"only found {len(hits)} install hints; the pattern has gone stale"
    assert any("sim" in h for h in hits)


def test_a_made_up_extra_is_caught() -> None:
    """The check itself, run against a string known to be wrong — so a regex that silently
    stopped matching cannot leave this file green."""
    match = INSTALL_HINT.search('pip install -e ".[record]"')

    assert match is not None
    assert match.group(1) == "record"
    assert "record" not in declared_extras()
