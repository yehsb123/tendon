"""Things that watch a run without steering it.

The recorder, the viewer, and the question of what video an episode will contain. Split out
of `cli/main.py` because they share one shape and one hazard: each is optional, each is
attached before the episode starts, and each has at some point existed while being wired to
nothing. The recorder subscribed to no bus; the bus had no subscriber; the viewer took a
frame source nobody passed. Keeping them together makes the pattern visible.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape


def attach_viewer(console: Console, bus, loaded, *, view: bool, save: str):
    """Stream the run into Rerun, when somebody asked for it.

    **Opt-in, unlike recording, and that difference is the point.** The recorder costs
    0.04 ms per step and is always attached because of it — design decision 1 is only
    structural because nobody would want it off. This costs about eighty times that, since
    it encodes frames a person will look at, and `services/viz.py` says so in its own
    docstring: attach it to a run being watched, not to every run being collected.

    So there is a flag here and none for recording. A flag on the wrong one of these would
    be the difference between a project that collects data and a project that means to.
    """
    if not view and not save:
        return None

    from tendon.services.viz import RerunLogger, VizError

    try:
        viewer = RerunLogger(
            session_name=f"tendon/{loaded.ref}",
            spawn=view,
            save_path=save or None,
            confidence_threshold=loaded.confidence_threshold,
        )
    except VizError as exc:
        # Not fatal. The run is still worth doing and still recorded; what is missing is
        # somewhere to watch it. Refusing would make an optional extra decide whether a
        # body moves.
        console.print(f"[yellow]not viewing: {escape(str(exc))}[/yellow]")
        return None

    viewer.attach_to(bus)
    if save:
        console.print(f"[dim]writing a Rerun recording to {escape(save)}[/dim]")
    return viewer


#: LeRobot's default when nothing is rendered. Never used to size a real frame — that comes
#: from the body — only to fill an argument the schema requires when there is no video.
NO_VIDEO_SIZE = (480, 640)


def video_schema(body) -> tuple[tuple[str, ...], tuple[int, int]]:
    """Which cameras this body is rendering, and at what size, asked once.

    Read from a real frame rather than from the body's declared `Capability.cameras`,
    because those are different questions: a body exposes cameras it is not rendering, and
    `features_for` is explicit that declaring one that will not be supplied turns every
    `add_frame` into an error. The frame is the only thing that knows.

    Nothing here is driver-specific. `RendersFrames` is the contract, `render()` names its
    own cameras, and the array says how big they are, so a driver written after this works
    without being added to a list.
    """
    from tendon.kernel.protocols import RendersFrames

    if not isinstance(body, RendersFrames):
        return (), NO_VIDEO_SIZE

    frames = body.render()
    if not frames:
        return (), NO_VIDEO_SIZE

    sample = next(iter(frames.values()))
    height, width = int(sample.shape[0]), int(sample.shape[1])
    return tuple(frames), (height, width)


def attach_recorder(console: Console, bus, loaded, store: str, body=None):
    """Subscribe a recorder to the step bus, or say why nothing is being recorded.

    Returns the recorder (None when unavailable) and the store path it is writing to.
    The caller opens each episode with `recorder.start(...)` and closes it with
    `finish()`: subscribing is per-run, but an episode is per-episode, and `eval` runs
    thirty of them through one subscription.

    Recording is not optional and there is no flag to turn it off, but LeRobot is an
    optional extra and the kernel and the simulator both work without it. So the one
    honest thing to do when it is missing is to run anyway and say plainly that this
    episode is not being kept. Failing the run would make an optional dependency
    mandatory; staying quiet would let someone collect nothing for an afternoon.
    """
    from tendon.services.store import DEFAULT_ROOT

    root = Path(store) if store else DEFAULT_ROOT

    try:
        from tendon.services.recorder import Recorder
    except ImportError:
        console.print("[yellow]not recording: LeRobot is not installed[/yellow]")
        console.print(
            "[dim]this episode will not be kept - " + escape('pip install -e ".[robot]"') + "[/dim]"
        )
        # No path either: naming a store nothing was written to is the same lie in a
        # quieter form.
        return None, None

    # Recorded under the skill's own reference rather than the recorder's default
    # `tendon/local`. Episodes are grouped by what was being done, which is what the
    # store's "skill" column claims to show, what `store.py` decodes a directory name
    # back into, and the only grouping a training run can use.
    from tendon.kernel.protocols import RendersFrames

    recorder = Recorder(root=root, repo_id=loaded.ref)
    # Pixels come from the body, not from the step: a `StepRecord` carries an
    # `Observation`, and an observation carries frame references rather than frames.
    # `services/` cannot import `drivers/` to go and fetch them, which is why the contract
    # this checks lives in the kernel.
    renders = body is not None and isinstance(body, RendersFrames)
    recorder.attach_to(bus, frames=body.render if renders else None)
    return recorder, root
