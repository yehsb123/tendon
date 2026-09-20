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
    """The two answers must be the same answer.

    This failed for real under a shuffled suite. `test_driver_arguments.py` replaced
    `drivers.base._REGISTRY` with a copy for the duration of a fixture; the first
    `available()` inside that window imported every driver module, so all three
    registrations landed in the copy, and the modules stayed in `sys.modules` so the
    lazy import never repeated. Teardown restored a dict snapshotted before any of them
    registered, and `available()` returned `()` for the rest of the process.

    Two of the failures it caused were the physical-body refusal tests below — safety
    coverage, switched off by whichever file pytest happened to run first.

    **What this can and cannot catch, now that `discover()` reads the registry too.** It
    catches a driver registered under a name that is not its module's, which is the case
    where `--driver <module>` would not find a body that exists. It no longer catches an
    empty registry, because both sides would be empty and agree — the test below is the one
    that holds that, and it is the one to read first if this file ever goes strange.
    """
    names = set(available())
    discovered = {i.name for i in discover() if i.available}
    assert names == discovered


def test_every_driver_module_that_imports_also_registers() -> None:
    """The invariant that actually caught the registry being emptied.

    It has to be stated directly rather than fall out of two functions agreeing. When the
    registry was wiped, *both* sides went empty: `available()` returned `()`, `discover()`
    now calls every module unregistered, and the comparison above passes on two empty sets
    while `test_a_physical_body_is_refused_by_default` finds no physical body and skips.
    Green, and testing nothing.

    No list of expected drivers, because that is the failure this whole file was written
    about. The claim is a shape: a module that imports must produce a driver.
    """
    unregistered = [
        info.name
        for info in discover()
        if info.unavailable_because and "registers no driver" in info.unavailable_because
    ]

    assert not unregistered, (
        f"{unregistered} import cleanly and register nothing. Every body would be "
        f"unopenable and most tests here would skip rather than fail."
    )


def test_a_module_that_imports_without_registering_is_not_called_available(monkeypatch) -> None:
    """`discover()` used to read import success as availability, so a module that produced
    no driver was listed as a working body.

    That is the shape that made the failure above invisible rather than merely present: the
    only two functions that could contradict each other were the two doing the contradicting.
    """
    from tendon.services import bodies

    monkeypatch.setattr(
        bodies,
        "_driver_modules",
        lambda: ("tendon.services.bodies",),  # imports, registers nothing
    )

    (info,) = discover()

    assert info.available is False
    assert info.unavailable_because
    assert "registers no driver" in info.unavailable_because


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
