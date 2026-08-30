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
    "is_simulated",
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

#: Which registered drivers declared themselves simulators. Absence means physical: a
#: driver that does not say counts as one that moves in the room.
_SIMULATED: set[str] = set()


def register(name: str, *, simulated: bool = False):
    """Register a driver implementation under the short name used by --driver.

    `simulated` is declared here rather than read from an instance because answering
    "is this a simulator?" must not require constructing the driver — `so101` wants a
    serial port, and opening one to ask a question would be the opposite of careful.

    The default is False. A driver that does not declare itself is treated as physical,
    because the cost of that being wrong is one flag and the cost of the opposite default
    is a real arm moving because someone ran an example.
    """

    def decorator(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"driver {name} is already registered")
        _REGISTRY[name] = cls
        if simulated:
            _SIMULATED.add(name)
        return cls

    return decorator


def is_simulated(name: str) -> bool:
    """Whether a registered driver declared itself a simulator, without constructing it."""
    return name in _SIMULATED


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
