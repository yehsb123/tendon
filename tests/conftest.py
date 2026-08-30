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

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _home_is_off_limits(tmp_path_factory: pytest.TempPathFactory):
    """The backstop, in place before any fixture of any scope runs.

    The per-test fixture below cannot cover module-scoped fixtures: those are set up
    outside a function-scoped `monkeypatch`, so a module fixture that builds a runtime gets
    the unpatched default. That is not a corner case — `test_shell_session.py` builds one
    exactly that way, and it reached the real home directory on the run that added this.
    """
    import tendon.services.memory_store as memory_store
    import tendon.services.store as store

    root = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store, "DEFAULT_ROOT", root / "episodes")
        patch.setattr(memory_store, "DEFAULT_MEMORY_ROOT", root / "memory")
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
    import tendon.services.memory_store as memory_store
    import tendon.services.store as store

    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path / "episodes")
    monkeypatch.setattr(memory_store, "DEFAULT_MEMORY_ROOT", tmp_path / "memory")
