import { useEffect } from "react";

import type { Body, Compatibility, ConnectionStatus, SkillSummary } from "../api/client";
import type { Intent, Observation } from "../api/types";
import { CorrectionEditor } from "../panels/CorrectionEditor";
import { IntentPreview } from "../panels/IntentPreview";
import { TrajectoryPreview } from "../panels/TrajectoryPreview";
import { useSession } from "../state/session";

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
    skills,
    chosenSkill,
    chosenBody,
    compatibility,
    choose,
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
      <PhysicalWarning body={bodies.find((b) => b.name === chosenBody)} />

      <ConnectionBanner
        status={status}
        detail={statusDetail}
        runtimeVersion={runtimeVersion}
        onStart={
          chosenSkill && chosenBody && compatibility?.compatible
            ? () => void start(chosenSkill, chosenBody)
            : undefined
        }
        running={session?.running ?? false}
      />

      {runtimeVersion !== null && !(session?.running ?? false) ? (
        <Chooser
          skills={skills}
          bodies={bodies}
          chosenSkill={chosenSkill}
          chosenBody={chosenBody}
          compatibility={compatibility}
          onChoose={(skill, body) => void choose(skill, body)}
        />
      ) : null}

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
 * Choosing what to run, and on what.
 *
 * The pairing is checked against the runtime before Start is offered. An operator invited
 * to begin a run that fails at load has been wasted twice: once waiting, once reading an
 * error that a question could have prevented.
 *
 * The reasons are shown rather than a bare "incompatible". "needs 6 axes, body has 5" is
 * something a person can act on; a greyed-out button is not.
 */
function Chooser({
  skills,
  bodies,
  chosenSkill,
  chosenBody,
  compatibility,
  onChoose,
}: {
  skills: SkillSummary[];
  bodies: Body[];
  chosenSkill: string | null;
  chosenBody: string | null;
  compatibility: Compatibility | null;
  onChoose: (skill: string | null, body: string | null) => void;
}) {
  return (
    <div className="chooser">
      <label>
        <span>skill</span>
        <select
          value={chosenSkill ?? ""}
          onChange={(event) => onChoose(event.target.value || null, chosenBody)}
        >
          <option value="">choose…</option>
          {skills.map((skill) => (
            <option key={skill.ref} value={skill.ref} disabled={Boolean(skill.error)}>
              {skill.ref}
              {skill.error ? " (does not load)" : ""}
            </option>
          ))}
        </select>
      </label>

      <label>
        <span>body</span>
        <select
          value={chosenBody ?? ""}
          onChange={(event) => onChoose(chosenSkill, event.target.value || null)}
        >
          <option value="">choose…</option>
          {bodies.map((body) => (
            <option key={body.name} value={body.name} disabled={!body.available}>
              {body.name}
              {body.simulated ? "" : " — real hardware"}
              {body.available ? "" : " (unavailable)"}
            </option>
          ))}
        </select>
      </label>

      {compatibility && !compatibility.compatible ? (
        <ul className="chooser-reasons" role="alert">
          {compatibility.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
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
  /** Absent until a compatible skill and body have been chosen. */
  onStart: (() => void) | undefined;
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
      {/* Offered only when something can actually be started. A button that explains
          its own refusal after being pressed is a button that should not have been
          pressable. */}
      {onStart !== undefined && !running ? (
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
