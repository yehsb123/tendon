"""Finding and opening bodies.

A thin layer over the driver registry, and the only place that knows which driver modules
exist to be imported.

## Why this is not in `api/` or `cli/`

`docs/architecture.md` forbids `api/` from importing `drivers/`, and the boundary test
enforces it. That rule caught a real duplication: the API and the CLI had each grown the
same `with suppress(ImportError): import tendon.drivers.mujoco` block, so adding a driver
would have meant remembering both.

Registration happens on import, so *something* has to import driver modules. Putting that
in one service means a new driver is added in one place, and the layers above keep asking
a question rather than performing an import.
"""

from __future__ import annotations

import contextlib
import pkgutil
from dataclasses import dataclass

from tendon.drivers import base as driver_base
from tendon.kernel.protocols import Driver

__all__ = ["BodyInfo", "BodyUnavailable", "available", "discover", "open_body"]

#: Modules in `tendon.drivers` that are not bodies.
_NOT_A_DRIVER = frozenset({"base"})


def _driver_modules() -> tuple[str, ...]:
    """Every driver module in the package, discovered rather than listed.

    An earlier version kept a hardcoded tuple, and the very first driver added after that
    — `human` — was missing from it. It registered itself correctly and was invisible to
    `doctor`, to `/api/bodies`, and to `--driver human`, with nothing reporting a problem
    because nothing knew it should exist.

    A list you have to remember to update is a list that will be wrong. Scanning the
    package cannot forget.
    """
    import tendon.drivers as package

    return tuple(
        f"tendon.drivers.{info.name}"
        for info in pkgutil.iter_modules(package.__path__)
        if not info.name.startswith("_") and info.name not in _NOT_A_DRIVER
    )


class BodyUnavailable(RuntimeError):
    """A body could not be opened.

    Wraps the driver-layer error so that `api/` and `cli/` can catch it precisely without
    importing `drivers/` — which `docs/architecture.md` forbids and the boundary test
    enforces. Without this they would be reduced to a bare `except Exception`, which
    catches a typo in a handler as readily as a missing extra.
    """


@dataclass(frozen=True)
class BodyInfo:
    """A body the runtime could open, without opening it."""

    name: str
    #: Why it is unavailable, when it is. None means it registered.
    unavailable_because: str | None = None

    @property
    def available(self) -> bool:
        return self.unavailable_because is None


def discover() -> tuple[BodyInfo, ...]:
    """Every driver module, and whether it registered.

    Reports the ones that failed to import alongside the ones that worked. A driver whose
    backend is missing is a different situation from a driver that does not exist, and a
    list that silently omits the first leaves someone wondering where it went.
    """
    infos: list[BodyInfo] = []
    for module in _driver_modules():
        short = module.rsplit(".", 1)[-1]
        try:
            __import__(module)
        except ImportError as exc:
            infos.append(BodyInfo(name=short, unavailable_because=str(exc)))
        else:
            infos.append(BodyInfo(name=short))
    return tuple(infos)


def available() -> tuple[str, ...]:
    """Names of bodies that can actually be opened."""
    for module in _driver_modules():
        with contextlib.suppress(ImportError):
            __import__(module)
    return driver_base.available()


def open_body(name: str, **kwargs) -> Driver:
    """Open a body by name.

    Raises `BodyUnavailable`, naming what is available — more useful than a bare
    KeyError when someone has a typo or a missing extra.
    """
    available()  # ensure registration has happened
    try:
        return driver_base.load(name, **kwargs)
    except driver_base.DriverError as exc:
        raise BodyUnavailable(str(exc)) from exc
