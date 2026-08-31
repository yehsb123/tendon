"""A body that needs an argument says which one, whatever body it is.

`tendon run --driver human` answered with a traceback ending in
`TypeError: HumanDriver.__init__() missing 1 required positional argument: 'repo_id'`.
The driver is offered by `--driver`, `doctor` lists it, and the only way to find out what
it wanted was to read its source.

## Why this is not a fix to the human driver

The point of an embodiment HAL is that a body nobody has written yet behaves like the ones
that exist. `SO101Driver` needs a serial port, `HumanDriver` needs a dataset, and the next
one will need something else — so the answer belongs in `open_body`, which is the single
place every body is opened through, and is derived from the driver's own signature rather
than from a list somebody has to remember to extend.

## Read from the signature, not from the exception

CPython's message names one missing argument at a time and its wording changes between
versions. A caller who has to run the command twice to discover two arguments has been told
half the answer.

## And it is not a missing install

`BodyUnavailable` was the obvious exception to reuse and it is wrong here: every caller that
catches it goes on to suggest installing a driver extra. A body that is present and
under-specified is not a missing install, and that advice would send somebody to reinstall
a driver they already have. Hence `MissingDriverArgument`, and a 400 rather than a 404 from
the API — the body exists; the request did not say enough.
"""

from __future__ import annotations

import pytest

from tendon.drivers import base as driver_base
from tendon.services.bodies import BodyUnavailable, MissingDriverArgument, open_body


@pytest.fixture
def demanding(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register a body that needs two arguments, one of which has a default.

    A made-up driver rather than a real one: this is a property of `open_body`, and pinning
    it to `human` would make it a test of that driver's current signature.
    """

    class Demanding:
        def __init__(self, repo_id, *, port, episode=0):
            self.repo_id = repo_id
            self.port = port

    registry = dict(driver_base._REGISTRY)
    registry["demanding"] = Demanding
    monkeypatch.setattr(driver_base, "_REGISTRY", registry)
    monkeypatch.setattr(driver_base, "is_simulated", lambda name: True)
    return "demanding"


def test_it_names_the_arguments_it_needs(demanding: str) -> None:
    with pytest.raises(MissingDriverArgument) as excinfo:
        open_body(demanding)

    message = str(excinfo.value)
    assert "repo_id" in message
    assert "port" in message


def test_it_names_all_of_them_at_once(demanding: str) -> None:
    """CPython reports one at a time. Two runs to learn two arguments is half an answer."""
    message = str(pytest.raises(MissingDriverArgument, lambda: open_body(demanding)).value)

    assert message.count("--driver-arg") == 2


def test_it_leaves_out_arguments_that_have_defaults(demanding: str) -> None:
    """`episode` defaults to 0. Listing it would tell somebody to supply something the
    driver is perfectly happy to choose."""
    message = str(pytest.raises(MissingDriverArgument, lambda: open_body(demanding)).value)

    assert "episode" not in message


def test_it_says_it_in_the_form_you_would_type(demanding: str) -> None:
    """`--driver-arg name=...` is the mechanism that exists for exactly this, and naming a
    parameter without naming how to pass it leaves the reader a step short."""
    message = str(pytest.raises(MissingDriverArgument, lambda: open_body(demanding)).value)

    assert "--driver-arg repo_id=" in message


def test_supplying_them_opens_the_body(demanding: str) -> None:
    body = open_body(demanding, repo_id="somewhere", port="COM3")

    assert body.repo_id == "somewhere"


def test_it_is_not_reported_as_a_missing_install(demanding: str) -> None:
    """The distinction that made this its own exception type.

    Every caller catching `BodyUnavailable` suggests installing a driver extra. Reusing it
    here would answer "your driver is not installed" about a driver that is.
    """
    with pytest.raises(MissingDriverArgument):
        open_body(demanding)

    assert not issubclass(MissingDriverArgument, BodyUnavailable)


def test_an_unknown_driver_is_still_a_missing_body() -> None:
    """The other side of that split stays where it was."""
    with pytest.raises(BodyUnavailable):
        open_body("no-such-body")


def test_a_driver_that_cannot_be_inspected_still_answers(monkeypatch) -> None:
    """A body whose signature cannot be read is unusual and not a reason to crash.

    Builtins and C extensions do this. The message is worse — it repeats the TypeError —
    and it is still a message rather than a traceback.
    """

    class Odd:
        def __new__(cls, *args, **kwargs):
            raise TypeError("missing 1 required positional argument: 'mystery'")

    registry = dict(driver_base._REGISTRY)
    registry["odd"] = Odd
    monkeypatch.setattr(driver_base, "_REGISTRY", registry)
    monkeypatch.setattr(driver_base, "is_simulated", lambda name: True)

    with pytest.raises(MissingDriverArgument) as excinfo:
        open_body("odd")

    assert "odd" in str(excinfo.value)
