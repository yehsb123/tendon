import { useEffect, useState } from "react";

import { api, type Progress as ProgressData } from "../api/client";

/**
 * The graph this project is measured by.
 *
 * `docs/roadmap.md`: *"Done when one graph exists. x-axis: cumulative human corrections.
 * y-axis: intervention rate. The line goes down."* It had been produced twice — by a
 * script and by a test — and never by the running system, so an operator correcting a
 * policy for a week had no way to see whether any of it was working.
 *
 * Drawn as SVG rather than with a charting library. It is one line and two axes; a
 * dependency that renders it would be larger than the shell.
 */
export function Progress() {
  const [runs, setRuns] = useState<ProgressData[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api.progress().then((result) => {
      if (cancelled) return;
      if (result.ok) setRuns(result.value);
      else setError(result.error);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error !== null) {
    return (
      <div className="placeholder">
        <h2>Progress</h2>
        <p className="hint hint-error">{error}</p>
      </div>
    );
  }

  if (runs === null) {
    return (
      <div className="placeholder">
        <h2>Progress</h2>
        <p>Reading what has happened so far…</p>
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="placeholder">
        <h2>Progress</h2>
        <p>Nothing has run yet.</p>
        <p>
          Start an episode from <strong>Live</strong>. When the policy is unsure it hands
          over; correct it, and this is where you find out whether it asks less afterwards.
        </p>
      </div>
    );
  }

  return (
    <div className="progress">
      <h2>Progress</h2>
      {runs.map((run) => (
        <Line key={`${run.skill}/${run.body}`} run={run} />
      ))}
    </div>
  );
}

function Line({ run }: { run: ProgressData }) {
  return (
    <section className="progress-run">
      <h3>
        {run.skill} <span className="skill-note">on {run.body}</span>
      </h3>

      <dl>
        <div>
          <dt>episodes</dt>
          <dd>{run.episodes}</dd>
        </div>
        <div>
          <dt>corrections</dt>
          <dd>{run.corrections}</dd>
        </div>
      </dl>

      {run.points.length === 0 ? (
        // Not an error and not an empty chart. A rate needs a full window before it means
        // anything, and saying how many more episodes that is beats an axis with nothing
        // on it.
        <p className="skill-note">
          {run.episodes} of {run.window} episodes. The rate is measured over a trailing
          window, so the line starts once there are {run.window}.
        </p>
      ) : (
        <Chart points={run.points} window={run.window} />
      )}
    </section>
  );
}

const WIDTH = 420;
const HEIGHT = 140;
const PAD = 28;

function Chart({
  points,
  window,
}: {
  points: { corrections: number; rate: number }[];
  window: number;
}) {
  const xs = points.map((p) => p.corrections);
  const maxX = Math.max(...xs, 1);
  // The y-axis is always 0 to 1. Scaling it to the data would make a fall from 30% to 20%
  // look like the same achievement as one from 100% to 0%.
  const scaleX = (x: number) => PAD + (x / maxX) * (WIDTH - PAD * 2);
  const scaleY = (y: number) => PAD + (1 - y) * (HEIGHT - PAD * 2);

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${scaleX(p.corrections)} ${scaleY(p.rate)}`).join(" ");

  const first = points[0];
  const last = points[points.length - 1];

  return (
    <>
      <svg
        className="progress-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={
          first && last
            ? `Intervention rate over ${window} episodes, ${Math.round(first.rate * 100)}% ` +
              `at ${first.corrections} corrections falling to ${Math.round(last.rate * 100)}% ` +
              `at ${last.corrections}`
            : "Intervention rate"
        }
      >
        <line x1={PAD} y1={scaleY(0)} x2={WIDTH - PAD} y2={scaleY(0)} className="axis" />
        <line x1={PAD} y1={scaleY(1)} x2={PAD} y2={scaleY(0)} className="axis" />
        <text x={4} y={scaleY(1) + 4} className="tick">
          100%
        </text>
        <text x={4} y={scaleY(0) + 4} className="tick">
          0%
        </text>
        <path d={path} className="progress-path" />
      </svg>
      <p className="skill-note">
        Intervention rate over the last {window} episodes, against corrections taught.
      </p>
    </>
  );
}
