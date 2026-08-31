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

__all__ = [
    "BodyInfo",
    "BodyUnavailable",
    "PhysicalBodyRefused",
    "available",
    "discover",
    "open_body",
]

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


class PhysicalBodyRefused(RuntimeError):
    """A real body was asked for without saying so explicitly.

    Not a safety mechanism — nothing here can stop a determined caller, and it is not
    meant to. It exists so that opening something that moves in the room is never a
    default, a typo, or a copy-pasted command. `SECURITY.md` says every safety limit in
    this repository has only ever held in simulation; the least this layer can do is make
    the person say they know which kind of body they are opening.
    """


class MissingDriverArgument(RuntimeError):
    """A driver exists and needs an argument nobody passed.

    A subclass of nothing in particular on purpose — `BodyUnavailable` is caught by callers
    that then suggest installing an extra, and this is the one case where that advice is
    wrong. A body that is present and under-specified is not a missing install.
    """


class BadDriverArgument(RuntimeError):
    """A driver argument was given a value the driver cannot use.

    Distinct from `MissingDriverArgument` for the reason that one is distinct from
    `BodyUnavailable`: the three are different situations and each has a different way
    out. Nothing is missing here — an argument was named and its value is the wrong shape,
    which is a typo to correct, not an install to perform or a parameter to discover.
    """


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
    #: False when this body moves real hardware. Read from the driver where possible;
    #: assumed physical when the driver cannot be constructed to ask.
    simulated: bool = False

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
            infos.append(BodyInfo(name=short, simulated=driver_base.is_simulated(short)))
    return tuple(infos)


def available() -> tuple[str, ...]:
    """Names of bodies that can actually be opened."""
    for module in _driver_modules():
        with contextlib.suppress(ImportError):
            __import__(module)
    return driver_base.available()


#: Values a driver parameter annotated `bool` accepts, since `bool("false")` is True and a
#: flag that is on however you spell it is worse than one that refuses the spelling.
_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})


def _coerce_argument(value: str, annotation: object, parameter: str) -> object:
    """Turn one command-line string into what the driver declared it wants.

    Reads the driver's own annotation rather than inspecting the string, which is the
    difference between asking and guessing. `--driver-arg port=8` stays `"8"` on a driver
    that annotates `port: str`, and becomes `8` on one that annotates `port: int`; nothing
    here decides that on its own.

    An unannotated or unrecognised parameter keeps its string. Leaving it alone is the
    honest default: this cannot know what the driver meant, and a string is what the
    caller actually typed.
    """
    import typing

    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        # `str | None`, and Optional[...] alike. The value came from a command line, so it
        # is not None; coerce to whichever member is not NoneType.
        members = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return _coerce_argument(value, members[0], parameter)
        return value

    if origin in (tuple, list, set, frozenset):
        # Comma-separated, because a shell splits on spaces and `--driver-arg` is one
        # token. Repeating the flag would collide with the several drivers that take more
        # than one sequence.
        items = [item.strip() for item in value.split(",") if item.strip()]
        inner = typing.get_args(annotation)
        element = inner[0] if inner and inner[0] is not Ellipsis else str
        coerced = [_coerce_argument(item, element, parameter) for item in items]
        return origin(coerced)

    if annotation is bool:
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise BadDriverArgument(
            f"{parameter}={value!r} is not a yes or a no. "
            f"Use one of: {', '.join(sorted(_TRUE | _FALSE))}"
        )

    if annotation in (int, float):
        try:
            return annotation(value)  # type: ignore[operator]
        except ValueError as exc:
            raise BadDriverArgument(
                f"{parameter} wants {annotation.__name__}, got {value!r}"  # type: ignore[union-attr]
            ) from exc

    return value


def coerce_driver_arguments(name: str, kwargs: dict[str, object]) -> dict[str, object]:
    """Convert string arguments to the types the named driver's signature declares.

    `--driver-arg` can only carry strings, so every driver parameter that is not one was
    unreachable from the command line. `MujocoDriver` takes `render_cameras: tuple[str,
    ...]`; passing `render_cameras=wrist` handed it a string, which it iterated character
    by character and refused as five unknown cameras. So a body that can record video had
    no way to be asked for it — the recording half of the project, unreachable through a
    parameter that was right there.

    The fix is at this layer rather than in a driver, for the same reason the missing-
    argument message is: whatever a driver declares should be reachable, including the
    drivers nobody has written yet. Nothing here is a per-driver table.

    Only strings are touched, so a Python caller passing real types is left alone.
    """
    import inspect
    import typing

    # Registration happens on import, and this function is public. Called before anything
    # had imported the driver modules it read every annotation as absent and handed back
    # the strings it was given — no error, no conversion, exactly the silent no-op it
    # exists to remove. `open_body` happens to do this first; a caller should not have to.
    available()

    try:
        # Typed loose deliberately. This is a class object out of the registry, and reading
        # `__init__` off it is exactly what `get_type_hints` needs, but mypy sees the
        # `type[Driver]` annotation and warns that an instance could rebind `__init__`.
        # It cannot here, and narrowing the registry's type to say so would be a larger
        # claim than this one call is worth.
        driver_cls: typing.Any = driver_base._REGISTRY[name]
        hints = typing.get_type_hints(driver_cls.__init__)
        parameters = inspect.signature(driver_cls).parameters
    except Exception:
        # A driver whose annotations do not resolve is not a reason to refuse to open it.
        # Strings are what the caller typed and what this function was handed.
        return kwargs

    resolved: dict[str, object] = {}
    for key, value in kwargs.items():
        if isinstance(value, str) and key in parameters and key in hints:
            resolved[key] = _coerce_argument(value, hints[key], key)
        else:
            resolved[key] = value
    return resolved


def camera_parameter(name: str) -> str | None:
    """The constructor parameter that says which cameras a driver should render, or None.

    Found on the driver rather than kept in a table here, so that telling somebody how to
    ask for video is either correct for their body or absent. Naming `render_cameras` at
    every driver would be right for MuJoCo and a lie for the next one.

    The convention is that the parameter's name ends in `cameras`, which `drivers/base.py`
    states as part of the contract. A weak convention that degrades to silence is worth
    more than a strong one nobody can discover: a driver that does not follow it loses a
    suggestion, not a capability.
    """
    import inspect

    available()
    try:
        driver = driver_base._REGISTRY[name]
        parameters = inspect.signature(driver).parameters
    except Exception:
        return None

    for parameter in parameters.values():
        if parameter.name.endswith("cameras"):
            return parameter.name
    return None


def open_body(name: str, *, allow_physical: bool = False, **kwargs) -> Driver:
    """Open a body by name.

    Raises `BodyUnavailable`, naming what is available — more useful than a bare
    KeyError when someone has a typo or a missing extra.

    Raises `PhysicalBodyRefused` when the body reports `simulated=False` and the caller
    did not pass `allow_physical`. A driver that does not declare itself simulated counts
    as physical: the cost of that being wrong is one flag, and the cost of the opposite
    default is a real arm moving because someone ran an example.

    The body is closed before refusing. Leaving a serial port open on the way out would
    make the second attempt fail for a different reason than the first.
    """
    known = available()  # ensure registration has happened

    # Existence first. Otherwise a typo produces "that is a physical body", which is both
    # wrong and confusing: an unregistered name is not physical, it is absent.
    if name not in known:
        raise BodyUnavailable(f"unknown driver {name!r}; available: {list(known)}")

    # Checked before construction, not after. An earlier version built the driver and then
    # inspected its capability, which meant a serial port was already open by the time the
    # refusal happened — touching the hardware in order to decide whether to touch it.
    # This is the whole reason `simulated` is declared at registration rather than read
    # from an instance.
    if not allow_physical and not driver_base.is_simulated(name):
        raise PhysicalBodyRefused(
            f"{name!r} is a physical body. Nothing here has been verified against real "
            "hardware and every safety limit has only ever held in simulation. See "
            "SECURITY.md. Pass allow_physical=True (CLI: --physical) if that is what you "
            "mean."
        )

    # After the physical check, before construction. A value the driver cannot use is
    # still a refusal to open a body, and refusing it here means it is refused identically
    # for every caller rather than once per command.
    kwargs = coerce_driver_arguments(name, kwargs)

    try:
        return driver_base.load(name, **kwargs)
    except driver_base.DriverError as exc:
        raise BodyUnavailable(str(exc)) from exc
    except TypeError as exc:
        # A driver that needs an argument nobody passed. `HumanDriver` needs `repo_id`
        # and `SO101Driver` needs a port, and `tendon run --driver human` used to answer
        # with a raw traceback ending in "missing 1 required positional argument".
        #
        # Handled here rather than per driver, because the point of the HAL is that a body
        # nobody has written yet behaves the same as the ones that exist: whatever it
        # requires, it says so, and `--driver-arg` is how it gets it.
        raise MissingDriverArgument(_missing_argument_message(name, exc)) from exc


def _missing_argument_message(name: str, exc: TypeError) -> str:
    """Say what the driver needs, in the form the caller would type.

    Reads the signature rather than parsing the exception text: the message CPython
    produces names one argument at a time and changes between versions, and a caller who
    has to run the command twice to discover two arguments has been told half the answer.
    """
    import inspect

    # Read from the registry `load` uses. `drivers/base.py` is Track A's and exposes no
    # lookup, and adding one for a message is not worth a change to their file — this
    # module already depends on that registry's shape through `load` and `is_simulated`.
    try:
        driver = driver_base._REGISTRY[name]
        parameters = inspect.signature(driver).parameters
    except Exception:  # noqa: BLE001 - a driver we cannot introspect still needs an answer
        return f"{name!r} could not be opened: {exc}"

    required = [
        parameter.name
        for parameter in parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    if not required:
        return f"{name!r} could not be opened: {exc}"

    args = " ".join(f"--driver-arg {parameter}=..." for parameter in required)
    return (
        f"{name!r} needs {', '.join(required)}. Pass {'it' if len(required) == 1 else 'them'} "
        f"with: {args}"
    )
