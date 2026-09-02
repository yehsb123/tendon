"""No test writes to the person running it.

Both stores default to the home directory, which is right for a runtime and wrong for a
test suite. `services/store.DEFAULT_ROOT` is `~/.tendon/episodes` and
`services/memory_store.DEFAULT_MEMORY_ROOT` is `~/.tendon/memory`, and anything that
constructs a runtime without naming a directory gets them.

This is not hypothetical. Adding the memory store put real files under a real
`~/.tendon/memory` on the first run of the suite — and then the *next* run loaded them, so
a policy that should have asked for help already knew the answers and nine tests failed
somewhere else entirely. The tests that were careful stayed green; the ones that were not
poisoned them.

## Why an autouse fixture rather than fixing the call sites

There were twenty call sites and the next one will be written by somebody who has not read
this file. A default that reaches into a home directory is a hazard whether or not each
author remembers it, and the check belongs where it cannot be forgotten. Tests that pass an
explicit root still work exactly as before — this only moves the *default*.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

#: Every module-level default that points into a home directory, as (module, attribute).
#:
#: A list rather than four lines of `setattr`, because `tests/test_home_is_guarded.py`
#: checks it against what is actually in `src/tendon/services/`. The guard was written one
#: round and a third store was added the next without updating it, which put real files
#: under a real `~/.tendon/progress` — the exact accident the guard exists to prevent,
#: repeated by the person who had just prevented it.
GUARDED_ROOTS = (
    ("tendon.services.store", "DEFAULT_ROOT", "episodes"),
    ("tendon.services.recorder", "DEFAULT_ROOT", "episodes"),
    ("tendon.services.memory_store", "DEFAULT_MEMORY_ROOT", "memory"),
    ("tendon.services.progress", "DEFAULT_PROGRESS_ROOT", "progress"),
    ("tendon.services.calibration", "DEFAULT_CALIBRATION_ROOT", "calibration"),
    # A file rather than a directory, and redirected for the opposite reason to the
    # others: nothing writes it, but a test that read the developer's real ceiling would
    # pass or fail depending on whose machine it ran on.
    ("tendon.services.limits", "DEFAULT_LIMITS_PATH", "limits.yaml"),
)


def _redirect(patch: pytest.MonkeyPatch, root: Path) -> None:
    """Point every guarded default under `root`."""
    import importlib

    for module_name, attribute, leaf in GUARDED_ROOTS:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            # An optional extra is missing, so nothing can write through it either.
            continue
        patch.setattr(module, attribute, root / leaf, raising=False)


@pytest.fixture(scope="session", autouse=True)
def _home_is_off_limits(tmp_path_factory: pytest.TempPathFactory):
    """The backstop, in place before any fixture of any scope runs.

    The per-test fixture below cannot cover module-scoped fixtures: those are set up
    outside a function-scoped `monkeypatch`, so a module fixture that builds a runtime gets
    the unpatched default. That is not a corner case — `test_shell_session.py` builds one
    exactly that way, and it reached the real home directory on the run that added this.
    """
    root = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        _redirect(patch, root)
        yield


@pytest.fixture(autouse=True)
def _no_writes_to_the_home_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both stores' defaults at this test's own temporary directory.

    Narrower than the session backstop above, and for a different reason: one default
    shared across the whole session would let one test's memory reach another's, which is
    how a control arm stopped asking for help in `test_shell_loop_closes.py`.

    Patched on the modules rather than on the environment because that is where the
    constants are read from, and a test that imports `DEFAULT_ROOT` directly should see the
    same value as one that lets `create_app` reach for it.
    """
    _redirect(monkeypatch, tmp_path)


#: Carried from sessionfinish to unconfigure, which is not handed the status.
_EXIT_STATUS: int | None = None


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Optionally end the process without running native teardown.

    Off unless `TENDON_EXIT_WITHOUT_TEARDOWN=1`, and set only on the CI job that installs
    the robot extra.

    That job reports every test passing and then dies: `terminate called without an active
    exception`, exit 134. `PYTHONFAULTHANDLER` put a stack to it, and the stack is the
    whole story -- `<no Python frame>`, with `torch._C` and the forty-odd `av` modules in
    the loaded list. The interpreter has finished; a native thread is being destroyed while
    still joinable as libraries tear down. The sibling job runs the same suite in the same
    run and installs neither library, and it exits cleanly.

    Four attempts were made to fix it as though it were ours: returning the body to the
    session, closing a duckdb connection nobody closed, cleaning up the registry, and
    pinning OpenMP to one thread. All four were reasonable and none of them changed the
    outcome, because the abort is in a dependency's shutdown and not in anything this
    repository executes.

    So this stops before that runs. `os._exit` skips atexit handlers, garbage collection
    and native static destructors -- pytest has finished by the time this hook is reached,
    every fixture has torn down, and the exit status is already decided, so nothing of ours
    is being skipped.

    What it does cost, and why it is opt-in rather than the default: anything that works
    after `sessionfinish` is skipped too, coverage reporting included. It is a real hammer,
    the kind that hides a genuine leak of our own by making its symptom disappear. Scoping
    it to one job keeps normal teardown everywhere else, so a shutdown bug in tendon's own
    code still shows up in the unit suite, locally, and in the sibling integration job.
    `PYTHONFAULTHANDLER` stays on either way, so a crash during a run is still reported.
    """
    global _EXIT_STATUS
    _EXIT_STATUS = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """Where the exit actually happens, and why it is not `sessionfinish`.

    The first version called `os._exit` from `sessionfinish` and truncated pytest's own
    summary line: the run showed its progress dots and then stopped, with no "N passed" to
    read. A green job that cannot say how many tests it ran is a worse trade than the
    abort it was avoiding, since the count is the thing that proves the suite did not
    quietly shrink.

    The terminal reporter writes that summary through its own writer during teardown, so
    the exit has to come after it. `unconfigure` is the last hook pytest calls, and the
    status is carried from `sessionfinish` because this one is not given it.
    """
    if os.environ.get("TENDON_EXIT_WITHOUT_TEARDOWN") != "1" or _EXIT_STATUS is None:
        return

    terminal = config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal._tw.flush()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_EXIT_STATUS)
