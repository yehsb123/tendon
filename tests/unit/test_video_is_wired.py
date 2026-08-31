"""A body that renders is asked for frames, and the recorder is told which cameras.

`Recorder.attach_to` has taken a `frames` callable since it was written, and its docstring
described it as "typically `MujocoDriver.render`" — naming a concrete driver from a layer
that is forbidden to import drivers, because there was no contract to name instead. Nothing
ever passed it. `recorder.start` has taken `cameras` for as long, and nothing ever passed
that either.

So both halves of video recording existed, neither end was connected, and the schema said
what it saw: state and actions, no images. The same shape as the bus that was created,
handed to the scheduler and never subscribed to.

`kernel.protocols.RendersFrames` is the contract that replaces the driver name. Whether a
body renders is `isinstance(body, RendersFrames)`, which holds for a driver nobody here has
written.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from rich.console import Console

from tendon.cli.main import _report_video, _video_schema
from tendon.kernel.protocols import RendersFrames


class _Blind:
    """A body with no `render` at all, which is most of them."""


class _Rendering:
    def __init__(self, frames: dict[str, Any]) -> None:
        self._frames = frames

    def render(self) -> dict[str, Any]:
        return self._frames


class _Capability:
    body_id = "test:body"

    def __init__(self, cameras: tuple[str, ...]) -> None:
        self.cameras = cameras


def test_a_body_that_renders_is_recognised_without_naming_its_class() -> None:
    assert isinstance(_Rendering({}), RendersFrames)
    assert not isinstance(_Blind(), RendersFrames)


def test_the_schema_follows_the_frame_not_the_declared_cameras() -> None:
    """Different questions. A body exposes cameras it is not rendering, and `features_for`
    is explicit that declaring one that will not be supplied turns every `add_frame` into
    an error. The frame is the only thing that knows which it is."""
    body = _Rendering({"wrist": np.zeros((240, 320, 3), dtype=np.uint8)})

    cameras, size = _video_schema(body)

    assert cameras == ("wrist",)
    assert size == (240, 320), "height, width - swapping these fails at the first frame"


def test_a_body_that_renders_nothing_declares_no_cameras() -> None:
    """A body that could render and was not asked to returns `{}`. Treated as "no video
    this run" rather than as an error, because rendering costs time per frame and a run
    that does not need it should not pay."""
    assert _video_schema(_Rendering({})) == ((), (480, 640))
    assert _video_schema(_Blind()) == ((), (480, 640))


def test_a_body_with_cameras_recording_none_says_so_while_it_can_be_changed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cost of not knowing is paid much later: at `tendon train`, four minutes into
    loading a checkpoint, about episodes recorded weeks earlier."""
    _report_video(Console(), (), _Capability(("wrist", "scene")), "mujoco")

    output = capsys.readouterr().out
    assert "no video" in output
    assert "render_cameras=wrist" in output, "the driver's own parameter, so it can be typed"


def test_a_body_with_no_cameras_is_not_told_off_every_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """It is not withholding anything."""
    _report_video(Console(), (), _Capability(()), "mujoco")

    assert capsys.readouterr().out == ""


def test_no_parameter_is_suggested_for_a_driver_that_does_not_take_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A suggestion right for MuJoCo and wrong for the next body is worse than none, and
    which one it is can be asked rather than assumed."""
    _report_video(Console(), (), _Capability(("wrist",)), "no-such-body")

    output = capsys.readouterr().out
    assert "no video" in output
    assert "--driver-arg" not in output


def test_the_recorder_is_handed_the_frames_source(monkeypatch) -> None:
    """The half that was never connected. Asserted on the call rather than on a recorded
    file, because the failure was that the argument was never passed at all."""
    import tendon.cli.main as cli

    passed: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def attach_to(self, bus: Any, frames: Any = None, **kwargs: Any) -> None:
            passed["frames"] = frames

    monkeypatch.setattr("tendon.services.recorder.Recorder", _Recorder)

    class _Loaded:
        ref = "grasp/cube-sim"

    body = _Rendering({"wrist": np.zeros((240, 320, 3), dtype=np.uint8)})
    cli._attach_recorder(Console(), object(), _Loaded(), "", body)
    assert passed["frames"] is not None, "a rendering body was attached with no frame source"

    cli._attach_recorder(Console(), object(), _Loaded(), "", _Blind())
    assert passed["frames"] is None, "a body that cannot render was asked to"
