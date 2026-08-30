import type { Intent, Observation } from "../api/types";

/**
 * What the arm is about to do, drawn.
 *
 * v0.2 turns on this panel being legible. `docs/roadmap.md` says so plainly: if a policy's
 * plan cannot be rendered as something an operator judges in a few seconds, the premise of
 * the shell is wrong and the project should stop. So the question this file answers is not
 * "how do we draw a chunk" but "what does a person need to see in two seconds".
 *
 * ## Not every joint equally
 *
 * A five-axis arm over a ten-step chunk is fifty numbers. Drawing five equally-weighted
 * curves produces something that is accurate and unreadable — an operator scans it, finds
 * no signal, and stops looking at the panel entirely, which is worse than not having it.
 *
 * So the axis that moves most is drawn solid and labelled with its travel; the rest are
 * drawn faint for context. "Which joint is doing the work, and how far" is the question a
 * person actually asks first.
 *
 * ## Where it starts matters
 *
 * The current position is marked. A trajectory shown without it says where the arm will go
 * and not whether that is a small adjustment or a lunge, and those need opposite reactions.
 */

export interface TrajectoryPreviewProps {
  intent: Intent | null;
  /** Where the body is now. The plan is read relative to this. */
  observation: Observation | null;
}

const WIDTH = 320;
const HEIGHT = 96;
const PADDING = 8;

/** Below this, a joint has not meaningfully moved [rad]. Roughly a hobby servo's step. */
const MOTION_EPSILON = 1e-3;

export function TrajectoryPreview({ intent, observation }: TrajectoryPreviewProps) {
  if (intent === null || intent.actions.length === 0) {
    return (
      <div className="trajectory trajectory-empty">
        <p>No plan to show.</p>
      </div>
    );
  }

  const series = toSeries(intent);
  if (series.length === 0) {
    return (
      <div className="trajectory trajectory-empty">
        <p>This action space is not a joint trajectory.</p>
      </div>
    );
  }

  const travels = series.map((values) => Math.max(...values) - Math.min(...values));
  const busiest = travels.indexOf(Math.max(...travels));
  const moving = travels[busiest] ?? 0;

  // One scale across every joint. Per-joint scaling would make a 0.002 rad wobble look
  // exactly like a 0.4 rad sweep, which is the single most misleading thing this panel
  // could do.
  const all = series.flat();
  const start = observation?.proprio.joint_positions ?? [];
  const low = Math.min(...all, ...start);
  const high = Math.max(...all, ...start);
  const span = Math.max(high - low, MOTION_EPSILON);

  const x = (index: number, length: number) =>
    PADDING + (index / Math.max(1, length - 1)) * (WIDTH - 2 * PADDING);
  const y = (value: number) =>
    HEIGHT - PADDING - ((value - low) / span) * (HEIGHT - 2 * PADDING);

  const path = (values: number[]) =>
    values.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i, values.length)} ${y(v)}`).join(" ");

  return (
    <div className="trajectory">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="trajectory-plot"
        role="img"
        aria-label={
          moving < MOTION_EPSILON
            ? "The arm is about to hold position."
            : `Joint ${busiest} moves ${moving.toFixed(3)} radians over this chunk.`
        }
      >
        {series.map((values, joint) =>
          joint === busiest ? null : (
            // eslint-disable-next-line react/no-array-index-key
            <path key={joint} d={path(values)} className="trajectory-line" />
          ),
        )}

        {/* Drawn last so it sits above the faint ones. */}
        <path d={path(series[busiest] ?? [])} className="trajectory-line is-busiest" />

        {start[busiest] !== undefined ? (
          <circle
            cx={x(0, series[busiest]?.length ?? 1)}
            cy={y(start[busiest])}
            r={3.5}
            className="trajectory-now"
          />
        ) : null}
      </svg>

      <p className="trajectory-caption">
        {moving < MOTION_EPSILON ? (
          "holding position"
        ) : (
          <>
            <strong>J{busiest}</strong> moves {moving.toFixed(3)} rad over{" "}
            {intent.horizon_s.toFixed(2)} s
          </>
        )}
      </p>
    </div>
  );
}

/**
 * Joint-space values per joint over the chunk.
 *
 * Empty for Cartesian action spaces: drawing an end-effector pose as though it were joint
 * angles would produce a plausible-looking picture of something that is not happening.
 */
function toSeries(intent: Intent): number[][] {
  const first = intent.actions[0];
  if (first === undefined) return [];
  if (first.space !== "joint_position" && first.space !== "joint_velocity") return [];

  const width = first.values.length;
  return Array.from({ length: width }, (_, joint) =>
    intent.actions.map((action) => action.values[joint] ?? 0),
  );
}
