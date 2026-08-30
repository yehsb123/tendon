import { IntentPreview } from "../panels/IntentPreview";
import type { ConnectionStatus } from "../api/socket";

/**
 * The view an operator watches during a shift.
 *
 * Answers one question at all times: what is it about to do, and should I let it?
 * Anything that does not serve that question belongs in another view.
 */
export function Live() {
  // The runtime does not exist yet (v0.1 is Track A work), so the shell shows the
  // disconnected state honestly rather than rendering an empty scene that looks live.
  const status: ConnectionStatus = "closed";

  return (
    <div className="live">
      <ConnectionBanner status={status} />
      <section className="live-scene" aria-label="Scene">
        <SceneUnavailable />
      </section>
      <aside className="live-side">
        <IntentPreview intent={null} status={status} />
      </aside>
    </div>
  );
}

/**
 * Connection state is surfaced, never hidden behind a spinner.
 *
 * An operator who cannot tell a frozen robot from a frozen UI reaches for the physical
 * stop, and that destroys the context the interrupt design exists to preserve.
 */
function ConnectionBanner({ status }: { status: ConnectionStatus }) {
  if (status === "open") return null;

  const message: Record<Exclude<ConnectionStatus, "open">, string> = {
    connecting: "Connecting to the runtime.",
    reconnecting:
      "Connection lost. The body is holding position and is not taking new intent.",
    closed: "Not connected to a runtime. Nothing shown here is live.",
  };

  return (
    <div className="banner" role="status" data-status={status}>
      <span className="banner-mark" aria-hidden="true" />
      {message[status]}
    </div>
  );
}

function SceneUnavailable() {
  return (
    <div className="scene-empty">
      <p>No scene. The Rerun viewer mounts here once a runtime is connected.</p>
      <p className="scene-empty-hint">
        The MuJoCo driver is Track A work — see <code>docs/collaboration.md</code>.
      </p>
    </div>
  );
}
