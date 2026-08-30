import { useEffect, useState } from "react";

import { api, type SkillDetail, type SkillSummary } from "../api/client";

/**
 * What is installed, and the terms each skill runs under.
 *
 * The safety limits are the reason this view exists. A skill declares the bounds every one
 * of its actions is checked against, including the ones an operator supplies — and until
 * now the only way to read them was to open `skill.yaml`. Somebody deciding whether to
 * approve a motion should be able to see what the motion is not allowed to do.
 *
 * A review view, so it can be dense. Nobody reads this while a robot is moving.
 */
export function Skills() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api.skills().then((result) => {
      if (cancelled) return;
      if (result.ok) setSkills(result.value);
      else setError(result.error);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selected === null) {
      setDetail(null);
      return;
    }

    let cancelled = false;
    const [namespace, name] = selected.split("/");
    if (!namespace || !name) {
      setError(`skill ref ${selected} is not namespace/name`);
      return;
    }

    void api.skill(namespace, name).then((result) => {
      if (cancelled) return;
      if (result.ok) setDetail(result.value);
      else setError(result.error);
    });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  if (error !== null) {
    return (
      <div className="placeholder">
        <h2>Skills</h2>
        <p className="hint hint-error">{error}</p>
      </div>
    );
  }

  if (skills === null) {
    return (
      <div className="placeholder">
        <h2>Skills</h2>
        <p>Reading the skill root…</p>
      </div>
    );
  }

  return (
    <div className="skills">
      <h2>Skills</h2>

      <ul className="skills-list">
        {skills.map((skill) => (
          <li key={skill.ref}>
            <button
              type="button"
              className="skills-entry"
              aria-current={selected === skill.ref ? "true" : undefined}
              onClick={() => setSelected(selected === skill.ref ? null : skill.ref)}
              /* A skill that does not load cannot be described, so opening it would show
                 an empty panel and no reason. The reason is on the row instead. */
              disabled={Boolean(skill.error)}
            >
              <span className="skills-ref">{skill.ref}</span>
              <span className="skills-version">{skill.version}</span>
              <span className="skills-summary">{skill.error ?? skill.summary}</span>
            </button>
          </li>
        ))}
      </ul>

      {detail ? <Detail detail={detail} /> : null}
    </div>
  );
}

/** The limits a skill asked for, as one readable line. */
function describe(limits: Record<string, number | number[] | null>): string {
  const set = Object.entries(limits).filter(([, value]) => value !== null);
  if (set.length === 0) return "no limits at all";

  return set
    .map(([key, value]) => `${key} ${Array.isArray(value) ? `[${value.join(", ")}]` : value}`)
    .join(", ");
}

function Detail({ detail }: { detail: SkillDetail }) {
  const limits = Object.entries(detail.safety).filter(([, value]) => value !== null);

  return (
    <section className="skill-detail">
      <h3>{detail.ref}</h3>

      <dl>
        <div>
          <dt>version</dt>
          <dd>{detail.version}</dd>
        </div>
        <div>
          <dt>licence</dt>
          <dd>{detail.license || "unstated"}</dd>
        </div>
        <div>
          <dt>hands over below</dt>
          {/* Labelled as a starting point on purpose: confidence is not calibrated across
              skills until v0.3, so this number is a configuration value an operator can
              move rather than a recommendation. */}
          <dd>
            {detail.confidence_threshold.toFixed(2)}{" "}
            <span className="skill-note">a starting point, not calibrated</span>
          </dd>
        </div>
        <div>
          <dt>evaluated over</dt>
          <dd>{detail.eval_episodes} episodes</dd>
        </div>
      </dl>

      <h4>Safety limits</h4>
      {detail.capped ? (
        // The numbers below are not the ones in `skill.yaml`. Said before them rather than
        // after: somebody comparing this screen against the file needs to know why they
        // differ before they conclude one of the two is wrong.
        <p className="skill-note">
          Narrowed by this machine's ceiling. The skill asked for{" "}
          {describe(detail.declared)}; what is enforced is below.
        </p>
      ) : null}
      {limits.length === 0 ? (
        // Worth saying loudly. A skill with no declared bounds is checked against nothing,
        // and an empty list would read as "nothing to see here".
        <p className="hint hint-error">
          None declared. Every action from this skill runs unbounded.
        </p>
      ) : (
        <ul className="skill-limits">
          {limits.map(([key, value]) => (
            <li key={key}>
              <code>{key}</code> {Array.isArray(value) ? `[${value.join(", ")}]` : String(value)}
            </li>
          ))}
        </ul>
      )}

      <h4>Success</h4>
      {detail.success_criteria.length === 0 ? (
        <p className="skill-note">
          No conditions declared, so runs of this skill cannot be judged — evaluation will
          report the success rate as not measurable.
        </p>
      ) : (
        <ul className="skill-limits">
          {detail.success_criteria.map((criterion) => (
            <li key={criterion.condition}>
              <code>{criterion.condition}</code> {criterion.threshold}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
