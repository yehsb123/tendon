"""The bus, tested for the property that matters: a subscriber cannot stop the robot.

Everything else here is fan-out plumbing. The isolation behaviour is the reason the module
exists — a recorder that fills the disk, a shell socket that drops, a curator that throws
on a malformed episode, none of these are reasons for a body to stop mid-motion, and all of
them would be if publish propagated.
"""

from __future__ import annotations

import pytest

from tendon.kernel.bus import Bus


def test_every_subscriber_receives_every_item() -> None:
    a: list[int] = []
    b: list[int] = []
    bus: Bus[int] = Bus()
    bus.subscribe("a", a.append)
    bus.subscribe("b", b.append)

    for i in range(3):
        bus.publish(i, step=i)

    assert a == [0, 1, 2]
    assert b == [0, 1, 2]


def test_publishing_with_no_subscribers_is_fine() -> None:
    """The scheduler must not have to check whether anyone is listening."""
    Bus[int]().publish(1)


def test_a_failing_subscriber_is_isolated_and_dropped() -> None:
    survived: list[int] = []
    bus: Bus[int] = Bus()

    def explode(_: int) -> None:
        raise RuntimeError("boom")

    bus.subscribe("broken", explode)
    bus.subscribe("fine", survived.append)

    bus.publish(1, step=7)
    bus.publish(2, step=8)

    assert survived == [1, 2], "a failing peer must not cost another subscriber its data"
    assert bus.subscribers == ("fine",), "the broken subscriber is dropped, not retried"
    assert len(bus.failures) == 1


def test_a_failure_records_where_and_what() -> None:
    """A run where the recorder died at step 12 produced 12 steps and looked normal."""
    bus: Bus[int] = Bus()

    def explode(_: int) -> None:
        raise OSError("no space left on device")

    bus.subscribe("recorder", explode)
    bus.publish(1, step=12)

    failure = bus.failures[0]
    assert failure.name == "recorder"
    assert failure.step == 12
    assert "OSError" in failure.error
    assert "no space left" in failure.error


def test_publish_never_raises_even_when_every_subscriber_fails() -> None:
    bus: Bus[int] = Bus()
    for name in ("one", "two"):
        bus.subscribe(name, lambda _: (_ for _ in ()).throw(ValueError("nope")))

    bus.publish(1)  # must not raise
    assert len(bus.failures) == 2
    assert bus.subscribers == ()


def test_a_dropped_subscriber_fails_only_once() -> None:
    """Dropping rather than retrying keeps a broken subscriber from filling the log and
    paying its exception cost on every single control step."""
    bus: Bus[int] = Bus()
    bus.subscribe("broken", lambda _: (_ for _ in ()).throw(RuntimeError("boom")))

    for i in range(10):
        bus.publish(i, step=i)

    assert len(bus.failures) == 1


def test_duplicate_names_are_refused() -> None:
    """Silently swapping a recorder for another would make episodes disappear with
    nothing to point at."""
    bus: Bus[int] = Bus()
    bus.subscribe("recorder", lambda _: None)
    with pytest.raises(ValueError):
        bus.subscribe("recorder", lambda _: None)


def test_unsubscribe_stops_delivery_and_tolerates_unknown_names() -> None:
    seen: list[int] = []
    bus: Bus[int] = Bus()
    bus.subscribe("a", seen.append)

    bus.publish(1)
    bus.unsubscribe("a")
    bus.unsubscribe("never-existed")
    bus.publish(2)

    assert seen == [1]


# ------------------------------------------------------------------------ measurement


def test_cost_is_measured_because_it_comes_out_of_the_control_loop() -> None:
    """Synchronous fan-out means subscriber time is loop time.

    Design decision 1 only holds if recording is close to free — a recorder that costs
    enough to notice is one that eventually gets switched off.
    """
    bus: Bus[int] = Bus()
    bus.subscribe("cheap", lambda _: None)

    for i in range(50):
        bus.publish(i, step=i)

    assert bus.mean_publish_cost() >= 0.0
    slowest = bus.slowest()
    assert slowest is not None
    assert slowest[0] == "cheap"


def test_slowest_names_the_worst_offender() -> None:
    bus: Bus[int] = Bus()
    bus.subscribe("fast", lambda _: None)
    bus.subscribe("slow", lambda _: sum(range(20000)))

    for i in range(5):
        bus.publish(i, step=i)

    slowest = bus.slowest()
    assert slowest is not None and slowest[0] == "slow"


def test_no_publications_means_no_cost_to_report() -> None:
    bus: Bus[int] = Bus()
    bus.subscribe("a", lambda _: None)
    assert bus.slowest() is None
    assert bus.mean_publish_cost() == 0.0
