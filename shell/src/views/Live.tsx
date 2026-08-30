import { useEffect } from "react";

import type { ConnectionStatus } from "../api/client";
import { IntentPreview } from "../panels/IntentPreview";
import { useSession } from "../state/session";

const SKILL = "skills/grasp/cube-sim";
const BODY = "mujoco";

/**
 * The view an operator watches during a shift.
 *
 * Answers one question at all times: what is it about to do, and should I let it?
 * Anything that does not serve that question belongs in another view.
 */
export function Live() {
  const {
    status,
    statusDetail,
    runtimeVersion,
    session,
    step,
    intent,
    pending,
    deciding,
    decisionError,
    checkRuntime,
    start,
    decide,
  } = useSession();

  useEffect(() => {
    void checkRuntime();
  }, [checkRuntime]);

  const handedOver = pending !== null;

  return (
    <div className="live">
      <ConnectionBanner
        status={status}
        detail={statusDetail}
        runtimeVersion={runtimeVersion}
        onStart={() => void start(SKILL, BODY)}
        running={session?.running ?? false}
      />

      <section className="live-scene" aria-label="Scene">
        <SceneView step={step} connected={status === "open"} />
      </section>

      <aside className="live-side">
        <IntentPreview
          intent={intent}
          status={status}
          onApprove={handedOver ? () => void decide("approved") : undefined}
          onReject={handedOver ? () => void decide("rejected") : undefined}
          onCorrect={handedOver ? () => void decide("rejected", undefined, "needs a correction") : undefined}
        />

        {deciding ? <p className="hint">sending…</p> : null}
        {decisionError ? (
          <p className="hint hint-error" role="alert">
            {decisionError}
          </p>
        ) : null}

        {session ? (
          <dl className="episode-summary">
            <div>
              <dt>episode</dt>
              <dd>{session.session_id.slice(0, 8)}</dd>
            </div>
            <div>
              <dt>step</dt>
              <dd>{step}</dd>
            </div>
            <div>
              <dt>interventions</dt>
              <dd>{session.interventions}</dd>
            </div>
          </dl>
        ) : null}
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
function ConnectionBanner({
  status,
  detail,
  runtimeVersion,
  onStart,
  running,
}: {
  status: ConnectionStatus;
  detail: string | null;
  runtimeVersion: string | null;
  onStart: () => void;
  running: boolean;
}) {
  if (status === "open") return null;

  const message: Record<Exclude<ConnectionStatus, "open">, string> = {
    connecting: "Connecting to the runtime.",
    reconnecting: "Connection lost. The body is holding position and is not taking new intent.",
    closed:
      runtimeVersion === null
        ? "No runtime. Start one with: tendon serve"
        : `Runtime ${runtimeVersion} is up. No episode is running.`,
  };

  return (
    <div className="banner" role="status" data-status={status}>
      <span className="banner-mark" aria-hidden="true" />
      <span>{message[status]}</span>
      {detail ? <span className="banner-detail">{detail}</span> : null}
      {runtimeVersion !== null && !running ? (
        <button type="button" className="btn btn-quiet" onClick={onStart}>
          Start an episode
        </button>
      ) : null}
    </div>
  );
}

function SceneView({ step, connected }: { step: number; connected: boolean }) {
  if (!connected) {
    return (
      <div className="scene-empty">
        <p>No scene. The Rerun viewer mounts here once a runtime is connected.</p>
        <p className="scene-empty-hint">
          Run <code>tendon serve</code>, then start an episode.
        </p>
      </div>
    );
  }

  return (
    <div className="scene-empty">
      <p>Episode running — step {step}.</p>
      <p className="scene-empty-hint">
        The Rerun viewer mounts here. Until then the intent panel carries the decision.
      </p>
    </div>
  );
}
