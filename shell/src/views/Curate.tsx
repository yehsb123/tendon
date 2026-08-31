import { useEffect, useState } from "react";

import { api, type Curation, type SkillSummary } from "../api/client";

/**
 * What is worth training on, and why.
 *
 * `curator.ScoredEpisode.reasons` describes itself as "shown in the shell, because a bare
 * number gives a reviewer nothing to disagree with". There was no shell view. The scores
 * were computed, the reasons were written, and the only way to read either was a command.
 *
 * A review view, so it can be dense — nobody reads this while a robot is moving. It ranks
 * and never deletes: an automated curator that is wrong about an episode is wrong about it
 * permanently, so the ordering is the output and the removal is a person's decision. That
 * is the whole reason the reasons column is wider than the score.
 */
export function Curate() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [curation, setCuration] = useState<Curation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api.skills().then((result) => {
      if (cancelled) return;
      if (result.ok) {
        setSkills(result.value);
        // One skill is the common case; making somebody click it first would be asking
        // a question with one answer.
        const only = result.value.length === 1 ? result.value[0] : undefined;
        if (only) setSelected(only.ref);
      } else setError(result.error);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selected === null) {
      setCuration(null);
      return;
    }

    const [namespace, name] = selected.split("/");
    if (!namespace || !name) {
      setError(`skill ref ${selected} is not namespace/name`);
      return;
    }

    let cancelled = false;
    void api.curation(namespace, name).then((result) => {
      if (cancelled) return;
      if (result.ok) setCuration(result.value);
      else setError(result.error);
    });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  if (error !== null) {
    return (
      <div className="placeholder">
        <h2>Curate</h2>
        <p className="hint hint-error">{error}</p>
      </div>
    );
  }

  if (skills === null) {
    return (
      <div className="placeholder">
        <h2>Curate</h2>
        <p>Reading the skill root…</p>
      </div>
    );
  }

  return (
    <div className="curate">
      <h2>Curate</h2>

      <ul className="skills-list">
        {skills.map((skill) => (
          <li key={skill.ref}>
            <button
              type="button"
              className="skills-entry"
              aria-current={selected === skill.ref ? "true" : undefined}
              onClick={() => setSelected(selected === skill.ref ? null : skill.ref)}
              disabled={Boolean(skill.error)}
            >
              <span className="skills-ref">{skill.ref}</span>
              <span className="skills-summary">{skill.error ?? skill.summary}</span>
            </button>
          </li>
        ))}
      </ul>

      {curation === null ? null : <Ranking curation={curation} />}
    </div>
  );
}

function Ranking({ curation }: { curation: Curation }) {
  if (curation.episodes.length === 0) {
    return (
      <p>
        Nothing recorded for this skill yet. Every run is kept, so start one from{" "}
        <strong>Live</strong> and it will be scored here.
      </p>
    );
  }

  const intervened = curation.episodes.filter((episode) => episode.had_interrupt).length;

  return (
    <>
      {intervened > 0 ? (
        // Said before the table, because the order is the first thing read and it is not
        // the order the scores would give. An unexplained ranking reads as a scoring
        // result, and this one deliberately overrides the scores.
        <p className="skill-note">
          {intervened} episode{intervened === 1 ? "" : "s"} an operator was handed control
          in {intervened === 1 ? "is" : "are"} first, whatever they scored — a score built
          from smoothness measures the wrong thing about a recording of recovery from
          failure.
        </p>
      ) : null}

      <table className="episodes-table">
        <thead>
          <tr>
            <th>episode</th>
            <th className="numeric">score</th>
            <th className="numeric">steps</th>
            <th>why</th>
          </tr>
        </thead>
        <tbody>
          {curation.episodes.map((episode) => (
            <tr key={episode.episode_id} data-interrupted={episode.had_interrupt || undefined}>
              <td>
                {episode.episode_id}
                {/* The one thing the curator values above every score, and it was only in
                    a DOM attribute — these episodes are promoted to the top and nothing
                    said why they were there. A reader would have taken the order for a
                    scoring result. */}
                {episode.had_interrupt ? (
                  <span className="episode-tag" title="An operator was handed control">
                    intervened
                  </span>
                ) : null}
              </td>
              <td className="numeric">{episode.score.toFixed(2)}</td>
              <td className="numeric">{episode.steps}</td>
              {/* The reasons, not the score, are what a reviewer argues with. An episode
                  with nothing notable about it is a normal episode, and saying so beats
                  an empty cell that reads like missing data. */}
              <td>
                {episode.reasons.length > 0 ? (
                  episode.reasons.join(", ")
                ) : (
                  <span className="skill-note">nothing notable</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {curation.interrupts_known ? null : (
        <p className="hint hint-error">
          This store cannot say which episodes were interrupted, so none were promoted.
          Those are the ones worth keeping most: they are the only recordings of recovery
          from failure.
        </p>
      )}
    </>
  );
}
