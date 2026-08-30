"""Driver registration, negotiation and faults.

Design decision 3. A driver is the only place in tendon that knows what kind of thing is
being moved. Policies address intent; drivers translate.

The `Driver` protocol itself is defined in `tendon.kernel.protocols`, not here: the
kernel owns the contract, drivers implement it. See that module for why. It is
re-exported below so driver authors have a single import.
"""

from __future__ import annotations

from tendon.kernel.protocols import Driver
from tendon.kernel.types import ActionSpace

__all__ = [
    "Driver",
    "DriverError",
    "ReadOnlyBody",
    "UnsupportedActionSpace",
    "available",
    "load",
    "negotiate",
    "register",
]


class DriverError(RuntimeError):
    """Base for driver faults. Raising during a run yields a DRIVER_FAULT interrupt."""


class ReadOnlyBody(DriverError):
    """Raised when a command is sent to a body that only produces observations."""


class UnsupportedActionSpace(DriverError):
    """Raised at load time when a skill needs an action space this body cannot accept.

    Deliberately a load-time failure. Discovering an incompatibility mid-episode means a
    robot is already moving when the mismatch is found.
    """


def negotiate(driver: Driver, required: tuple[ActionSpace, ...]) -> ActionSpace:
    """Pick the action space a skill and a body agree on, or fail before anything moves.

    Preference order is the driver order, not the skill order: the body knows which of
    its accepted spaces it executes most faithfully.
    """
    for space in driver.accepts:
        if space in required:
            return space
    raise UnsupportedActionSpace(
        f"body {driver.capability.body_id} accepts {list(driver.accepts)}, "
        f"skill requires one of {list(required)}"
    )


_REGISTRY: dict[str, type] = {}


def register(name: str):
    """Register a driver implementation under the short name used by --driver."""

    def decorator(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"driver {name} is already registered")
        _REGISTRY[name] = cls
        return cls

    return decorator


def available() -> tuple[str, ...]:
    """Driver names importable in this environment.

    Drivers register on import and driver modules are imported lazily, so this reflects
    which optional extras are installed.
    """
    return tuple(sorted(_REGISTRY))


def load(name: str, **kwargs) -> Driver:
    """Instantiate a registered driver."""
    if name not in _REGISTRY:
        raise DriverError(f"unknown driver {name!r}; available: {list(available())}")
    return _REGISTRY[name](**kwargs)
