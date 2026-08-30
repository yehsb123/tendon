"""Rerun logging — what an episode looked like while it happened.

`docs/stack.md` draws the line: Rerun *shows* data, and the shell renders what has not
happened yet. This module is the first half. It streams an episode into a Rerun recording
so an operator, or anyone reading a failure afterwards, can see the run rather than read a
table of it.

## What is logged that a generic logger would not log

LeRobot ships `utils/rerun_visualization.py`, which logs observations and actions. That
covers the body. It does not cover the three things tendon exists to make visible:

**Commanded against applied, on the same axes.** `Driver.apply` returns what the body
actually executed, which differs from the command whenever hardware clips. Plotted
together, the gap between the two lines is the body refusing an instruction — invisible in
any log that records only one of them, and the reason the `apply` contract was changed.

**Confidence over time, against the threshold that would raise an interrupt.** The v0.3
graph is intervention rate against corrections; the per-episode version of that question is
"how close did this run come to asking for help, and where?" A flat line at 0.9 with one
dip at step 212 says something a mean does not.

**Where safety clamped, and what it could not check.** `safety.check` reports limits it
could not evaluate rather than passing them silently. An episode that ran partly unverified
should look different from one that did not.

## Cost, measured

Attach this for a run being watched. Do not attach it to every run being collected.

A 430-step episode with one 240x320 camera, against a 10 ms control budget:

    uncompressed   13.8 MB   2.24 ms/step   22% of budget
    jpeg q75        0.5 MB   3.29 ms/step   33% of budget

Compression is on by default: 27x smaller for one extra millisecond. A viewer log that
outgrows the dataset it describes gets deleted, and the ability to review failures goes
with it.

Both numbers are large. `services/recorder.py` costs 0.04 ms per step and is always
attached because of it; this costs eighty times that and is not. The difference is what
each one is for — the recorder produces training data, and this produces something a human
looks at once.

Requires the view extra:  pip install "tendon-os[view]"
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tendon.kernel.types import Intent, InterruptContext, InterruptResolution

# Entity paths. Rerun groups by prefix, so these decide the shape of the default layout
# as much as the blueprint does.
_BODY = "body"
_COMMANDED = "action/commanded"
_APPLIED = "action/applied"
_CONFIDENCE = "policy/confidence"
_EVENTS = "events"
_CAMERA = "camera"


class VizError(RuntimeError):
    """Raised when a recording cannot be opened."""


class RerunLogger:
    """Streams steps, intents and interrupts into a Rerun recording.

    Attach with `attach_to`, the same way the recorder attaches. Both can subscribe to one
    bus; neither knows about the other.
    """

    def __init__(
        self,
        session_name: str = "tendon",
        *,
        spawn: bool = False,
        save_path: str | Path | None = None,
        frames: Callable[[], dict[str, Any]] | None = None,
        confidence_threshold: float = 0.5,
        compress_images: bool = True,
        jpeg_quality: int = 75,
    ) -> None:
        """
        Args:
            session_name: Recording name, shown in the viewer.
            spawn: Open the Rerun viewer. False by default: a collection run on a robot
                host has no display, and a logger that tries to open a window there fails
                for a reason unrelated to what it was logging.
            save_path: Write an `.rrd` file instead of, or as well as, streaming. This is
                what makes a failure reviewable after the fact — the run is over, the
                robot has been reset, and the recording is all that is left.
            frames: Callable returning `{camera: array}`, typically `MujocoDriver.render`.
                Same injection as the recorder, and for the same reason: `services` cannot
                import `drivers`, and an `Observation` carries frame references rather
                than pixels.
            confidence_threshold: Drawn as a horizontal reference on the confidence plot.
                Should match the scheduler's, or the line means nothing.
            compress_images: JPEG-encode camera frames before logging. On by default
                because the uncompressed cost is not small: a 430-step episode with one
                240x320 camera writes 14.6 MB raw. Collection produces episodes by the
                hundred, and a viewer log that outgrows the dataset it describes will be
                deleted, taking the ability to review failures with it.
            jpeg_quality: Encoder quality, 0-100. This log is for a human deciding what
                happened, not for training — the dataset holds the frames a policy learns
                from, and `services/recorder.py` writes those losslessly.
        """
        try:
            import rerun as rr
        except ImportError as exc:  # pragma: no cover - depends on the view extra
            raise VizError(
                'rerun is not installed. Install the view extra: pip install "tendon-os[view]"'
            ) from exc

        self._rr = rr
        self._frames = frames
        self._threshold = float(confidence_threshold)
        self._compress_images = compress_images
        self._jpeg_quality = int(jpeg_quality)
        self._closed = False

        rr.init(session_name)
        if save_path is not None:
            path = Path(save_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            rr.save(str(path))
        if spawn:
            rr.spawn()

        self._send_blueprint()

    # ------------------------------------------------------------------ layout

    def _send_blueprint(self) -> None:
        """Lay the views out once, rather than accepting Rerun's default grid.

        The default puts every entity in its own panel in discovery order, which scatters
        the two halves of a comparison. Commanded and applied belong on one plot or the
        gap between them is not visible, which is the whole reason both are logged.
        """
        rr = self._rr
        try:
            import rerun.blueprint as rrb
        except ImportError:  # pragma: no cover - older rerun without blueprints
            return

        blueprint = rrb.Blueprint(
            rrb.Horizontal(
                rrb.Spatial2DView(origin=_CAMERA, name="cameras"),
                rrb.Vertical(
                    rrb.TimeSeriesView(
                        name="commanded vs applied",
                        contents=[f"{_COMMANDED}/**", f"{_APPLIED}/**"],
                    ),
                    rrb.TimeSeriesView(name="confidence", contents=[f"{_CONFIDENCE}/**"]),
                    rrb.TimeSeriesView(name="body", contents=[f"{_BODY}/**"]),
                    rrb.TextLogView(name="events", origin=_EVENTS),
                ),
            )
        )
        rr.send_blueprint(blueprint)

    # -------------------------------------------------------------- attachment

    def attach_to(self, bus: Any, *, name: str = "rerun") -> None:
        """Subscribe to the scheduler's step bus.

        `name` appears in `EpisodeResult.subscriber_failures` if this logger raises. A
        visualiser that dies mid-episode must not be able to take the run with it, and the
        bus already guarantees that; naming it means the failure is attributable.
        """
        bus.subscribe(name, self.log_step)

    # ---------------------------------------------------------------- logging

    def log_step(self, record: Any) -> None:
        """Log one control step. Called at control rate, so it stays flat."""
        if self._closed:
            return
        rr = self._rr
        rr.set_time("step", sequence=record.step)

        proprio = record.observation.proprio
        rr.log(f"{_BODY}/joint_positions", rr.Scalars(list(proprio.joint_positions)))
        if proprio.joint_velocities is not None:
            rr.log(f"{_BODY}/joint_velocities", rr.Scalars(list(proprio.joint_velocities)))
        if proprio.gripper_open is not None:
            rr.log(f"{_BODY}/gripper_open", rr.Scalars(float(proprio.gripper_open)))

        # Both, always, and on the same time axis. The gap is the measurement.
        self._log_action(_COMMANDED, record.commanded)
        self._log_action(_APPLIED, record.applied)

        if getattr(record, "clamped", False):
            rr.log(
                _EVENTS,
                rr.TextLog("safety clamped this action", level=rr.TextLogLevel.WARN),
            )
        for limit in getattr(record, "unchecked", ()):
            rr.log(
                _EVENTS,
                rr.TextLog(f"unchecked: {limit}", level=rr.TextLogLevel.DEBUG),
            )

        if self._frames is not None:
            for camera, pixels in self._frames().items():
                image = rr.Image(pixels)
                if self._compress_images:
                    image = image.compress(jpeg_quality=self._jpeg_quality)
                rr.log(f"{_CAMERA}/{camera}", image)

    def _log_action(self, path: str, action: Any) -> None:
        rr = self._rr
        rr.log(f"{path}/joints", rr.Scalars(list(action.values)))
        if action.gripper is not None:
            rr.log(f"{path}/gripper", rr.Scalars(float(action.gripper)))

    def log_intent(self, intent: Intent, *, step: int) -> None:
        """Log a chunk before it executes, with its confidence.

        Called from wherever the intent is produced rather than from the step bus, because
        `StepRecord` carries no confidence — it is per-step and confidence is a property of
        the chunk. Noted in `docs/collaboration.md` as something the scheduler may end up
        publishing instead.
        """
        if self._closed:
            return
        rr = self._rr
        rr.set_time("step", sequence=step)

        rr.log(f"{_CONFIDENCE}/score", rr.Scalars(float(intent.confidence.score)))
        rr.log(f"{_CONFIDENCE}/threshold", rr.Scalars(self._threshold))
        rr.log(f"{_CONFIDENCE}/horizon_s", rr.Scalars(float(intent.horizon_s)))

        if intent.confidence.reasons:
            # Logged as text rather than folded into the score, because the reasons are
            # what an operator acts on. A number says handover; the reason says why.
            rr.log(
                _EVENTS,
                rr.TextLog(
                    f"confidence {intent.confidence.score:.3f} "
                    f"({intent.confidence.source.value}): " + "; ".join(intent.confidence.reasons),
                    level=rr.TextLogLevel.INFO,
                ),
            )

    def log_interrupt(
        self, context: InterruptContext, resolution: InterruptResolution, *, step: int
    ) -> None:
        """Log a handover and how it was resolved.

        The most valuable moments in an episode, and the ones a plot of joint angles does
        not show at all.
        """
        if self._closed:
            return
        rr = self._rr
        rr.set_time("step", sequence=step)
        rr.log(
            _EVENTS,
            rr.TextLog(
                f"INTERRUPT {context.reason.value} at step {context.step} "
                f"-> {resolution.resolution.value}"
                + (f": {resolution.note}" if resolution.note else ""),
                level=rr.TextLogLevel.WARN,
            ),
        )

    def close(self) -> None:
        """Flush and stop. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        # Best effort. A viewer that has already gone away must not turn tidying up into
        # the thing that fails an otherwise successful episode.
        with contextlib.suppress(Exception):
            self._rr.rerun_shutdown()
