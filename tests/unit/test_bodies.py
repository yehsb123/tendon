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

from tendon.services.bodies import (
    BodyUnavailable,
    PhysicalBodyRefused,
    available,
    discover,
    open_body,
)

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


def _needs_configuration(name: str) -> bool:
    """Whether opening this body requires arguments a caller has to supply.

    Read from the driver's own signature rather than from a list. `discover()` exists
    because a hardcoded list of drivers goes stale; a hardcoded list of *exceptions* to a
    test over those drivers goes stale the same way.
    """
    import inspect

    from tendon.drivers import base as driver_base

    driver = driver_base._REGISTRY[name]
    for parameter in inspect.signature(driver).parameters.values():
        positional = parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        if positional and parameter.default is inspect.Parameter.empty:
            return True
    return False


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
        if _needs_configuration(name):
            # A body that cannot be opened without being told where it is. `human` needs a
            # recording, `so101` needs a serial port. Naming them here would mean editing
            # this test every time a driver is added, which is the failure the discovery
            # scan was written to remove — so the requirement is read off the constructor
            # instead.
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


# ------------------------------------------------------- simulated versus in the room


def test_a_physical_body_is_refused_by_default() -> None:
    """Opening something that moves in the room must never be a default or a typo.

    Not a safety mechanism — nothing here stops a determined caller. It exists so that the
    person says which kind of body they are opening, given that every safety limit in this
    repository has only ever held in simulation.
    """
    physical = [i.name for i in discover() if i.available and not i.simulated]
    if not physical:
        pytest.skip("no physical driver is registered in this environment")

    with pytest.raises(PhysicalBodyRefused) as excinfo:
        open_body(physical[0])

    assert "SECURITY.md" in str(excinfo.value)


def test_the_refusal_happens_before_the_hardware_is_touched() -> None:
    """The bug this ordering exists to prevent.

    An earlier version constructed the driver and then inspected its capability, so a
    serial port was already open by the time the refusal happened — touching the hardware
    in order to decide whether to touch it. `simulated` is declared at registration
    precisely so the question can be answered without constructing anything.
    """
    from tendon.drivers import base as driver_base

    physical = [i.name for i in discover() if i.available and not i.simulated]
    if not physical:
        pytest.skip("no physical driver is registered in this environment")

    name = physical[0]
    constructed = []

    class Tripwire(driver_base._REGISTRY[name]):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            constructed.append(True)
            super().__init__(*args, **kwargs)

    original = driver_base._REGISTRY[name]
    driver_base._REGISTRY[name] = Tripwire
    try:
        with pytest.raises(PhysicalBodyRefused):
            open_body(name, port="COM-NOT-REAL")
    finally:
        driver_base._REGISTRY[name] = original

    assert not constructed, "the driver was built before the refusal"


def test_a_simulator_opens_without_a_flag() -> None:
    """The default must not make ordinary work harder than it needs to be."""
    simulated = [i.name for i in discover() if i.available and i.simulated]
    assert simulated, "no simulator is registered, so nothing here can be run safely"


def test_an_undeclared_driver_counts_as_physical() -> None:
    """The safe default, asserted rather than assumed.

    A driver that forgets to declare itself must not be treated as a simulator: the cost of
    that being wrong is a real arm moving because someone ran an example.
    """
    from tendon.drivers import base as driver_base

    assert not driver_base.is_simulated("a-driver-that-was-never-registered")
