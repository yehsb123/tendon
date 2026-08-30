import { useState } from "react";

import type { Intent } from "../api/types";
import { applyOffsets, isTouched, jointCount, nudge as nudgeOffsets } from "./correction";

/**
 * Showing the robot what to do instead.
 *
 * Opens on the intent the operator just refused, and edits it as a per-joint offset
 * applied across the whole chunk. Editing every step individually would be more expressive
 * and unusable: a ten-step chunk on a five-axis arm is fifty numbers, and the person
 * doing this has seconds and one hand.
 *
 * An offset is also the shape of the correction people actually give — "go a bit higher",
 * "come in further left" — rather than a redrawn trajectory.
 *
 * ## Why the correction is sent whole
 *
 * The runtime receives a complete `Intent`, not a delta. A delta would need the runtime to
 * reconstruct what it was relative to, and a reconstruction that is even slightly wrong is
 * a motion nobody chose. The arithmetic happens here, where the operator can see the
 * resulting numbers before committing.
 */

export interface CorrectionEditorProps {
  /** What the policy proposed. The correction is built from this. */
  intent: Intent;
  onCancel: () => void;
  onSubmit: (correction: Intent, note: string) => void;
  busy?: boolean | undefined;
}

export function CorrectionEditor({ intent, onCancel, onSubmit, busy }: CorrectionEditorProps) {
  const width = jointCount(intent);
  const [offsets, setOffsets] = useState<number[]>(() => new Array(width).fill(0));
  const [note, setNote] = useState("");

  const nudge = (joint: number, direction: 1 | -1) => {
    setOffsets((current) => nudgeOffsets(current, joint, direction));
  };

  const touched = isTouched(offsets);

  const submit = () => {
    onSubmit(applyOffsets(intent, offsets), note.trim());
  };

  return (
    <section className="correction" aria-label="Correction">
      <h3 className="correction-title">Show it what to do</h3>
      <p className="correction-hint">
        Adjust each axis. The offset applies to the whole chunk.
      </p>

      <ul className="correction-joints">
        {offsets.map((offset, joint) => (
          // Index is the identity here: these are joint slots on a fixed body, not a
          // reorderable list.
          // eslint-disable-next-line react/no-array-index-key
          <li key={joint} className="correction-joint">
            <span className="correction-joint-label">J{joint}</span>
            <button
              type="button"
              className="btn btn-quiet btn-nudge"
              onClick={() => nudge(joint, -1)}
              aria-label={`joint ${joint} down`}
            >
              −
            </button>
            <span className="correction-offset" data-changed={offset !== 0}>
              {offset >= 0 ? "+" : ""}
              {offset.toFixed(3)} rad
            </span>
            <button
              type="button"
              className="btn btn-quiet btn-nudge"
              onClick={() => nudge(joint, 1)}
              aria-label={`joint ${joint} up`}
            >
              +
            </button>
          </li>
        ))}
      </ul>

      <label className="correction-note">
        <span>Why (recorded with the correction)</span>
        <input
          type="text"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="approach from the left"
        />
      </label>

      <footer className="correction-actions">
        <button type="button" className="btn btn-quiet" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-accent"
          onClick={submit}
          /* An unchanged correction is an approval wearing the wrong label, and it would
             be stored as a lesson that teaches the policy nothing. */
          disabled={busy || !touched}
        >
          {busy ? "Sending…" : "Send correction"}
        </button>
      </footer>
    </section>
  );
}
