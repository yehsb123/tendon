import type { ConnectionStatus } from "../api/socket";
import type { Intent } from "../api/types";

/**
 * What the robot is about to do.
 *
 * The centre of the project. Everything else in the shell supports this panel.
 *
 * Its constraint is the operator, not the framework: someone on a floor has a couple of
 * seconds and one hand free. A change that makes this more informative but slower to read
 * is a regression.
 *
 * Three states, and the container reserves the same height for all of them. A layout that
 * shifts when confidence drops costs exactly the seconds this panel exists to save, and it
 * shifts at the worst moment — when a decision is suddenly required.
 */

export type PreviewState = "confident" | "uncertain" | "interrupted";

const CONFIDENCE_FLOOR = 0.5;

export interface IntentPreviewProps {
  intent: Intent | null;
  status: ConnectionStatus;
  /** Present only when control has actually been handed over. */
  onApprove?: () => void;
  onReject?: () => void;
  onCorrect?: () => void;
}

export function IntentPreview({
  intent,
  status,
  onApprove,
  onReject,
  onCorrect,
}: IntentPreviewProps) {
  const state = previewState(intent, Boolean(onApprove));

  return (
    <section className="intent" data-state={state} aria-label="Intended action">
      <header className="intent-head">
        <h2 className="intent-title">{title(state)}</h2>
        <ConfidenceReadout intent={intent} />
      </header>

      <div className="intent-body">
        {intent ? <IntentSummary intent={intent} /> : <NoIntent status={status} />}
      </div>

      {/* The reasons block is rendered in every state and hidden when empty, rather than
          mounted on demand. Mounting it would move everything below it. */}
      <div className="intent-reasons" data-empty={!intent?.confidence.reasons.length}>
        {intent?.confidence.reasons.map((reason) => (
          <p key={reason} className="intent-reason">
            {reason}
          </p>
        ))}
      </div>

      <footer className="intent-actions" data-active={state === "interrupted"}>
        {state === "interrupted" ? (
          <>
            {/* Approve is not the visually dominant control. An operator under time
                pressure defaults to the biggest button, and the default here should be
                deliberation, not consent. */}
            <button type="button" className="btn btn-quiet" onClick={onReject}>
              Reject
            </button>
            <button type="button" className="btn btn-quiet" onClick={onCorrect}>
              Correct
            </button>
            <button type="button" className="btn btn-accent" onClick={onApprove}>
              Approve
            </button>
          </>
        ) : (
          <p className="intent-actions-idle">
            No decision required. Controls appear only when control is handed over.
          </p>
        )}
      </footer>
    </section>
  );
}

function previewState(intent: Intent | null, handedOver: boolean): PreviewState {
  if (handedOver) return "interrupted";
  if (!intent) return "confident";
  return intent.confidence.score < CONFIDENCE_FLOOR ? "uncertain" : "confident";
}

function title(state: PreviewState): string {
  switch (state) {
    case "interrupted":
      return "Your decision";
    case "uncertain":
      return "About to act — unsure";
    case "confident":
      return "About to act";
  }
}

/**
 * Confidence, as a classification first and a number second.
 *
 * Three steps rather than a continuous scale: an operator classifies, they do not read a
 * percentage off a colour ramp. The number is there for the review views afterwards.
 *
 * The band carries a label as well as a colour, because colour is never the only signal.
 */
function ConfidenceReadout({ intent }: { intent: Intent | null }) {
  if (!intent) {
    return (
      <div className="confidence" data-band="none">
        <span className="confidence-band">—</span>
      </div>
    );
  }

  const score = intent.confidence.score;
  const band = score >= 0.75 ? "high" : score >= CONFIDENCE_FLOOR ? "medium" : "low";
  const label = band === "high" ? "sure" : band === "medium" ? "unsure" : "needs you";

  return (
    <div className="confidence" data-band={band}>
      <span className="confidence-band">{label}</span>
      <span className="confidence-score" aria-label="confidence score">
        {score.toFixed(2)}
      </span>
    </div>
  );
}

function IntentSummary({ intent }: { intent: Intent }) {
  return (
    <dl className="intent-summary">
      <div>
        <dt>Goal</dt>
        <dd>{intent.goal ?? "unstated"}</dd>
      </div>
      <div>
        <dt>Target</dt>
        <dd>{intent.target ?? "unstated"}</dd>
      </div>
      <div>
        <dt>Horizon</dt>
        {/* Units are always shown. A bare number in a robotics interface is a future
            incident, and the same rule applies here as in the Python. */}
        <dd>
          {intent.horizon_s.toFixed(2)} s · {intent.actions.length} steps
        </dd>
      </div>
    </dl>
  );
}

function NoIntent({ status }: { status: ConnectionStatus }) {
  return (
    <p className="intent-empty">
      {status === "open"
        ? "The policy has not issued an action chunk yet."
        : "Not receiving intent. Nothing here is live."}
    </p>
  );
}
