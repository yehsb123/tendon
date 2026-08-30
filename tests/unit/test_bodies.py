"""Driver discovery.

The property that matters: **a driver that exists is found.** An earlier version kept a
hardcoded list of driver modules, and the first driver added after that — `human` — was
missing from it. It registered itself correctly and was invisible to `doctor`, to
`/api/bodies`, and to `--driver human`, with nothing reporting a problem because nothing
knew it should exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tendon.services.bodies import BodyUnavailable, available, discover, open_body

DRIVERS_DIR = Path(__file__).resolve().parents[2] / "src" / "tendon" / "drivers"
#: Modules in the package that are not bodies.
NOT_DRIVERS = {"base", "__init__"}


def modules_on_disk() -> set[str]:
    return {
        path.stem
        for path in DRIVERS_DIR.glob("*.py")
        if path.stem not in NOT_DRIVERS and not path.stem.startswith("_")
    }


def test_every_driver_module_on_disk_is_discovered() -> None:
    """The regression. A list you have to remember to update is a list that will be wrong.

    This compares against the filesystem, so adding a driver makes it pass without anyone
    editing a registry — and forgetting to register one makes it fail.
    """
    discovered = {info.name for info in discover()}
    assert discovered == modules_on_disk()


def test_discovery_reports_why_a_driver_is_unavailable() -> None:
    """A driver whose backend is missing is a different situation from one that does not
    exist, and a list that silently omits the first leaves someone wondering."""
    for info in discover():
        if not info.available:
            assert info.unavailable_because, f"{info.name} is unavailable with no reason"


def test_available_returns_only_loadable_bodies() -> None:
    names = set(available())
    discovered = {i.name for i in discover() if i.available}
    assert names == discovered


def test_opening_an_unknown_body_names_what_exists() -> None:
    """More useful than a bare KeyError when someone has a typo or a missing extra."""
    with pytest.raises(BodyUnavailable) as excinfo:
        open_body("nosuch")

    message = str(excinfo.value)
    assert "nosuch" in message
    for name in available():
        assert name in message


def test_base_is_not_mistaken_for_a_body() -> None:
    assert "base" not in {info.name for info in discover()}


def test_a_discovered_body_can_actually_be_opened() -> None:
    """Discovery that reports a body it cannot open would be worse than not reporting it.

    "Cannot open" has two meanings and only one of them is a bug. A body whose backend is
    not installed is *correctly* unavailable — that is what the extras are for, and what
    `doctor` reports. A body that is discoverable, has its backend, and still fails is the
    failure this test exists to catch.

    So a missing backend is not skipped over silently: the error still has to name the
    install that would fix it, which is the difference between a useful message and a
    stack trace. This also keeps `tests/unit` runnable with no simulator, which
    `CONTRIBUTING.md` requires and the CI unit job depends on.
    """
    for name in available():
        if name == "human":
            # Read-only and needs a recording; construction requires arguments.
            continue
        try:
            body = open_body(name)
        except BodyUnavailable as exc:
            assert "install" in str(exc).lower(), (
                f"{name} is unavailable but does not say how to get it: {exc}"
            )
            continue
        try:
            assert body.capability.body_id
        finally:
            body.close()
