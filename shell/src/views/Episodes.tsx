import { useEffect, useState } from "react";

import { api, type Episode } from "../api/client";

/**
 * What has been recorded.
 *
 * A review view rather than an operating one: nobody reads this while a robot is moving,
 * so it can be as dense as it needs to be. That is the split `views/README.md` describes —
 * `Live` answers one question for someone with seconds, everything else serves whoever is
 * improving the system afterwards.
 *
 * An empty store is not an error and does not get an error's treatment. It is the normal
 * state before anything has run, and the useful thing to show is what to do about it.
 */
export function Episodes() {
  const [episodes, setEpisodes] = useState<Episode[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    void api.episodes().then((result) => {
      if (cancelled) return;
      if (result.ok) setEpisodes(result.value);
      else setError(result.error);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  if (error !== null) {
    return (
      <div className="placeholder">
        <h2>Episodes</h2>
        <p className="hint hint-error">{error}</p>
      </div>
    );
  }

  if (episodes === null) {
    return (
      <div className="placeholder">
        <h2>Episodes</h2>
        <p>Reading the store…</p>
      </div>
    );
  }

  if (episodes.length === 0) {
    return (
      <div className="placeholder">
        <h2>Episodes</h2>
        <p>Nothing recorded yet.</p>
        <p>
          Every run is recorded — there is no collection mode to switch on. Start an
          episode from <strong>Live</strong> and it will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="episodes">
      <h2>Episodes</h2>
      <table className="episodes-table">
        <thead>
          <tr>
            <th>skill</th>
            <th className="numeric">episodes</th>
            <th className="numeric">size</th>
            <th>last written</th>
          </tr>
        </thead>
        <tbody>
          {episodes.map((dataset) => (
            <tr key={dataset.ref} data-unreadable={!dataset.readable}>
              <td>{dataset.ref}</td>
              {/* A count that could not be read is not zero. Showing 0 would say the
                  recording is empty when what happened is that nobody could tell. */}
              <td className="numeric">{dataset.episodes ?? "—"}</td>
              <td className="numeric">{dataset.size}</td>
              <td>{new Date(dataset.modified).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {episodes.some((d) => !d.readable) ? (
        <ul className="episodes-problems">
          {episodes
            .filter((d) => !d.readable)
            .map((d) => (
              <li key={d.ref}>
                <strong>{d.ref}</strong>: {d.detail}
              </li>
            ))}
        </ul>
      ) : null}
    </div>
  );
}
