"""`--driver-arg` could only carry strings, so most of what a driver declares was unreachable.

`MujocoDriver` takes `render_cameras: tuple[str, ...]`. Passing `render_cameras=wrist`
handed it the string, which it iterated character by character and refused as five unknown
cameras. So the body that can record video had no way to be asked for it, and the store
`tendon run` filled had no `observation.images.*` at all — which is where `tendon train`
died, four minutes into loading a checkpoint, on episodes recorded weeks earlier.

The old reasoning was that guessing types would mean deciding `port=8` is an int on a body
where it is a name, and that a driver knows its own argument types. Both true. The
conclusion did not follow: the driver's signature can be read, so nothing has to guess.
"""

from __future__ import annotations

import pytest

from tendon.services.bodies import (
    BadDriverArgument,
    available,
    camera_parameter,
    coerce_driver_arguments,
)


@pytest.fixture(autouse=True)
def _registered() -> None:
    available()


def test_a_sequence_parameter_takes_a_comma_separated_list() -> None:
    """A shell splits on spaces and `--driver-arg` is one token, so a list is commas."""
    assert coerce_driver_arguments("mujoco", {"render_cameras": "wrist"}) == {
        "render_cameras": ("wrist",)
    }
    assert coerce_driver_arguments("mujoco", {"render_cameras": "wrist,scene"}) == {
        "render_cameras": ("wrist", "scene")
    }


def test_numbers_and_flags_follow_the_annotation() -> None:
    coerced = coerce_driver_arguments("mujoco", {"render_hz": "30", "gripper_opens_high": "no"})

    assert coerced["render_hz"] == 30.0
    assert isinstance(coerced["render_hz"], float)
    assert coerced["gripper_opens_high"] is False


def test_a_string_parameter_is_left_alone() -> None:
    """The case the old docstring was defending, and it still holds: `gripper_actuator`
    is annotated `str | None`, so a value that looks like a number stays text."""
    coerced = coerce_driver_arguments("mujoco", {"gripper_actuator": "8"})

    assert coerced["gripper_actuator"] == "8"


def test_an_unknown_parameter_keeps_its_string() -> None:
    """Not this layer's refusal to make. Construction raises `TypeError` and `open_body`
    turns that into a message naming what the driver does take, which is a better answer
    than one from here about a parameter it knows nothing about."""
    assert coerce_driver_arguments("mujoco", {"nonsense": "7"}) == {"nonsense": "7"}


def test_a_value_the_annotation_cannot_take_is_refused_by_name() -> None:
    with pytest.raises(BadDriverArgument) as caught:
        coerce_driver_arguments("mujoco", {"render_hz": "fast"})

    assert "render_hz" in str(caught.value)
    assert "float" in str(caught.value)


def test_a_flag_that_is_neither_yes_nor_no_is_refused() -> None:
    """`bool("false")` is True, so a flag that is on however you spell it is worse than
    one that refuses the spelling."""
    with pytest.raises(BadDriverArgument):
        coerce_driver_arguments("mujoco", {"gripper_opens_high": "maybe"})


def test_values_that_are_not_strings_are_left_alone() -> None:
    """A Python caller passing real types is not going through a command line."""
    assert coerce_driver_arguments("mujoco", {"render_cameras": ("wrist",)}) == {
        "render_cameras": ("wrist",)
    }


def test_an_unregistered_driver_returns_what_it_was_given() -> None:
    """A driver whose annotations cannot be read is not a reason to refuse: strings are
    what the caller typed, and construction will say what is wrong with them."""
    assert coerce_driver_arguments("no-such-body", {"a": "1"}) == {"a": "1"}


def test_the_camera_parameter_is_found_on_the_driver_not_in_a_table_here() -> None:
    """So that telling somebody how to ask for video is either right for their body or
    absent. Naming `render_cameras` for every driver would be right for MuJoCo and a lie
    for the next one."""
    assert camera_parameter("mujoco") == "render_cameras"
    assert camera_parameter("no-such-body") is None
