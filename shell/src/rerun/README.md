# rerun/ — embedded viewer

Wraps `@rerun-io/web-viewer`. Rerun renders the scene, the robot, and time-aligned sensor
streams. We do not reimplement any of that.

## Where Rerun stops

Rerun shows what happened or is happening. It has no notion of what is *about to* happen,
of confidence, or of a decision awaiting a human.

So this directory does two things: it mounts the viewer and keeps its clock aligned with
the episode timeline, and it exposes the coordinate frame so `panels/IntentPreview` can
draw the proposed trajectory in the same space as the rendered scene.

Anything not yet real is drawn by the overlay, never by Rerun. Keeping that line sharp is
what stops a proposal from being mistaken for a measurement.
