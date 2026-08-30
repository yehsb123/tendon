"""The step bus.

The scheduler publishes each control step; the recorder, the shell stream and anything
else subscribe. None of them holds a reference to another, which is what lets a subscriber
be added without touching the control loop.

Design decision 1 is structural because of this file. Recording is not a mode that can be
switched off — it is a subscriber that is always attached. There is no flag to forget.

## The rule that shapes this

**A subscriber must never be able to stop the robot.**

A recorder that fills the disk, a shell socket that drops, a curator that throws on a
malformed episode — none of these are reasons for a body to stop mid-motion, and all of
them would be if `publish` propagated exceptions. So a failing subscriber is isolated,
recorded, and skipped for the rest of the episode. The scheduler is told, and decides.

The inverse matters too: a slow subscriber blocks the control loop, because this is
synchronous fan-out. Subscribers on the hot path must enqueue and return. That is a
contract this module cannot enforce, so it measures instead — `slowest` names the worst
offender, which is enough to find it.

## Why synchronous

An asyncio bus would decouple subscriber latency from the loop, and add an event loop to a
kernel that otherwise has none. Fan-out to three callables that each enqueue takes
microseconds. If that ever stops being true, the measurement is already here to prove it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

__all__ = ["Bus", "SubscriberFailure"]

T = TypeVar("T")


@dataclass(frozen=True)
class SubscriberFailure:
    """A subscriber that raised, and was dropped for the rest of the run."""

    name: str
    #: Step index at which it failed.
    step: int
    error: str


@dataclass
class Bus(Generic[T]):
    """Synchronous fan-out with subscriber isolation.

    Generic over the published type so the same machinery carries step records now and
    observations or intents later, without the kernel growing a second bus.
    """

    _subscribers: dict[str, Callable[[T], None]] = field(default_factory=dict)
    _failures: list[SubscriberFailure] = field(default_factory=list)
    _durations: dict[str, float] = field(default_factory=dict)
    _published: int = 0

    def subscribe(self, name: str, handler: Callable[[T], None]) -> None:
        """Attach a subscriber under a name used in failure reports.

        Names are unique. Re-subscribing under an existing name is a programming error
        rather than a replacement: silently swapping a recorder for another would make
        episodes disappear with nothing to point at.
        """
        if name in self._subscribers:
            raise ValueError(f"a subscriber named {name!r} is already attached")
        self._subscribers[name] = handler

    def unsubscribe(self, name: str) -> None:
        self._subscribers.pop(name, None)

    def publish(self, item: T, *, step: int = 0) -> None:
        """Deliver to every subscriber. Never raises.

        A subscriber that throws is dropped and recorded. Delivery continues to the rest:
        one failing consumer must not cost the others their data, and none of them is a
        reason to stop a moving body.
        """
        self._published += 1

        for name in list(self._subscribers):
            handler = self._subscribers[name]
            started = time.perf_counter()
            try:
                handler(item)
            except Exception as exc:  # noqa: BLE001 — isolation is the whole point
                self._failures.append(
                    SubscriberFailure(name=name, step=step, error=f"{type(exc).__name__}: {exc}")
                )
                del self._subscribers[name]
            else:
                elapsed = time.perf_counter() - started
                self._durations[name] = self._durations.get(name, 0.0) + elapsed

    @property
    def failures(self) -> tuple[SubscriberFailure, ...]:
        """Subscribers that raised and were dropped.

        The scheduler surfaces these on the episode result. A run where the recorder died
        at step 12 produced 12 steps of data and looked otherwise normal, and nobody
        should have to notice that by finding a short file later.
        """
        return tuple(self._failures)

    @property
    def subscribers(self) -> tuple[str, ...]:
        return tuple(self._subscribers)

    def slowest(self) -> tuple[str, float] | None:
        """The subscriber that spent the most total time, and how much [s].

        Synchronous fan-out means this time came out of the control loop. Returns None
        when nothing has been published.
        """
        if not self._durations:
            return None
        name = max(self._durations, key=lambda k: self._durations[k])
        return name, self._durations[name]

    def mean_publish_cost(self) -> float:
        """Mean total subscriber time per published item [s].

        The number that decides whether design decision 1 is free. If recording costs
        enough to be noticed, it will eventually be switched off, and then there is no
        data — see `examples/01_record`.
        """
        if not self._published:
            return 0.0
        return sum(self._durations.values()) / self._published
