"""Shared fixtures for the integration suite.

## Why `no_recorder` exists

Opening and closing a LeRobotDataset costs about thirteen seconds per episode on this
machine. Measured directly: the identical session flow takes 13.46s with a recorder and
0.27s without, and none of the difference is behaviour any of these files assert about.

It had grown into the dominant cost of the whole suite. One test in
`test_abandoned_episode.py` took 23.6 seconds against 71 for the other 673, and four more
files were paying about twenty seconds each — for recording that not one of them makes a
single assertion about.

## Opt-in rather than automatic

Applied by naming it, so a file that *does* test recording — `test_shell_session.py`,
`test_cli_run.py`, `test_cli_curate.py` — simply does not ask for it and keeps the real
thing. An autouse fixture here would have silently switched recording off underneath those,
which is a worse failure than a slow suite: they would still pass, and stop meaning
anything.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def no_recorder() -> None:
    """Run sessions without writing episodes.

    For files whose subject is what happens *around* an episode — the disconnect, the
    memory, the progress log, the body being released — rather than what is written during
    one.

    Module-scoped, and that is the whole reason it works. A function-scoped fixture is set
    up after a module-scoped one, so the files here that build their sessions in a
    `scope="module"` fixture would get the real recorder anyway: `test_progress.py` stayed
    at 17 seconds with a per-test version applied, all of it inside the fixture's setup.
    The same scope mismatch bit `tests/conftest.py` when the home-directory guard first went
    in.

    `pytest.MonkeyPatch.context()` rather than the `monkeypatch` fixture, which is
    function-scoped and cannot be requested from here.
    """
    import tendon.api.app as app_module

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(app_module, "_open_recorder", lambda loaded, root: None)
        yield
