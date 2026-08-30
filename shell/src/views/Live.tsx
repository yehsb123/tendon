import { useEffect } from "react";

import type { Body, ConnectionStatus } from "../api/client";
import type { Intent, Observation } from "../api/types";
import { CorrectionEditor } from "../panels/CorrectionEditor";
import { IntentPreview } from "../panels/IntentPreview";
import { TrajectoryPreview } from "../panels/TrajectoryPreview";
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
    correcting,
    setCorrecting,
    observation,
    bodies,
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
      <PhysicalWarning body={bodies.find((b) => b.name === BODY)} />

      <ConnectionBanner
        status={status}
        detail={statusDetail}
        runtimeVersion={runtimeVersion}
        onStart={() => void start(SKILL, BODY)}
        running={session?.running ?? false}
      />

      <section className="live-scene" aria-label="Scene">
        <SceneView
          step={step}
          connected={status === "open"}
          intent={intent}
          observation={observation}
        />
      </section>

      <aside className="live-side">
        {correcting && pending ? (
          <CorrectionEditor
            intent={pending.intent}
            busy={deciding}
            onCancel={() => setCorrecting(false)}
            onSubmit={(correction, note) => void decide("corrected", correction, note)}
          />
        ) : (
          <IntentPreview
            intent={intent}
            status={status}
            onApprove={handedOver ? () => void decide("approved") : undefined}
            onReject={handedOver ? () => void decide("rejected") : undefined}
            onCorrect={handedOver ? () => setCorrecting(true) : undefined}
          />
        )}

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
 * A body that moves in the room does not look like one in a window.
 *
 * Shown before anything else and never dismissible. An operator approving a motion needs
 * to know which kind of body they are approving it for, and finding that out afterwards
 * is the wrong order.
 *
 * A body the runtime has not described is treated as physical, matching the runtime's own
 * default: the safe direction to be wrong in.
 */
function PhysicalWarning({ body }: { body: Body | undefined }) {
  if (body === undefined || body.simulated) return null;

  return (
    <div className="banner banner-physical" role="alert">
      <span className="banner-mark" aria-hidden="true" />
      <span>
        <strong>{body.name}</strong> moves real hardware. Nothing here has been verified
        against a real body, and every safety limit has only ever held in simulation.
      </span>
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

function SceneView({
  step,
  connected,
  intent,
  observation,
}: {
  step: number;
  connected: boolean;
  intent: Intent | null;
  observation: Observation | null;
}) {
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
    <div className="scene-live">
      <TrajectoryPreview intent={intent} observation={observation} />
      <p className="scene-empty-hint">
        step {step} — the Rerun viewer will render the body here; the plan above is what it
        is about to do.
      </p>
    </div>
  );
}
