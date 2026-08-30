#!/usr/bin/env python
"""Run what CI runs, before pushing instead of after.

Nothing in this repository mirrored the CI jobs, so the only way to find out whether a
push was clean was to push it. That produced two red pushes in a row, neither of them
interesting: one line over the limit, then a formatter complaint on the fix for it.

Two things this does that chaining the commands by hand does not:

Every check runs. Stopping at the first failure is how the second red push happened --
`ruff check && ruff format --check` short-circuited on an unrelated pre-existing finding,
so the formatter never ran and the push went out on a check that had not been performed.

Failures are reported against the files they are in. In a shared working tree, most of
what a linter finds belongs to the other track and is not yours to fix; a summary that
does not separate the two is a summary you learn to skip.

    python scripts/check.py            # everything
    python scripts/check.py --fast     # skip the test suite

Run it plainly. `python scripts/check.py | tail -4 && git push` looks like a guarded push
and is not one: a pipeline reports the exit status of its last command, so `tail` succeeds
and `&&` proceeds no matter what this script found. That is how a push went out while this
was reporting a failure. The summary is already short enough not to need trimming.

The node jobs (shell/) are not included: they need npm and a separate install.
"""

from __future__ import annotations

import os
import subprocess
import sys

#: Kept in step with .github/workflows/ci.yml by hand. If a job there changes and this
#: does not, this becomes a check that passes while CI fails, which is worse than nothing.
CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lint", ("ruff", "check", "src", "tests", "scripts")),
    ("format", ("ruff", "format", "--check", "src", "tests", "scripts")),
    ("types", ("mypy", "src/tendon", "--ignore-missing-imports")),
    ("unit", ("pytest", "tests/unit", "-q")),
)


def printable(text: str) -> str:
    """Drop what the console cannot encode, rather than dying on it.

    ruff draws its diagnostics with box characters. A Korean-locale Windows console is
    cp949, which has no U+2502, so printing a ruff failure raised UnicodeEncodeError from
    the script whose entire purpose is to report failures. The first run of this file
    crashed on its own output.

    Fourth time this encoding has decided what a program is allowed to say, so it is
    handled here instead of avoided by convention.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


#: A CI runner has no terminal, so rich renders plain text there. A developer console
#: usually does, and rich then wraps its output in ANSI colour. Three tests assert on
#: what a user is shown -- an install hint, two remedies -- and they fail locally against
#: escape codes CI never produces. That is a check reporting a failure that does not
#: exist, which is the fastest way to teach someone to ignore it.
#:
#: These two variables are what makes the local run mean the same thing as the remote one.
CI_ENVIRONMENT = {"NO_COLOR": "1", "TERM": "dumb"}


def run(name: str, command: tuple[str, ...]) -> tuple[str, bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", *command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **CI_ENVIRONMENT},
    )
    return name, proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def main() -> int:
    checks = CHECKS[:-1] if "--fast" in sys.argv else CHECKS
    results = [run(name, command) for name, command in checks]

    for name, ok, output in results:
        print(f"[{'ok  ' if ok else 'FAIL'}] {name}")
        if not ok:
            print(printable("\n".join("    " + line for line in output.splitlines()[-40:])))

    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print(f"\n{len(failed)} of {len(results)} failed: {', '.join(failed)}")
        print("Check whether the findings are in your files. In a shared tree they")
        print("often are not, and CI only sees what has been committed.")
        return 1

    print(f"\nall {len(results)} clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
