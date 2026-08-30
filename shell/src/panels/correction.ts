/**
 * Building the correction an operator sends.
 *
 * Separated from the editor UI because this is the arithmetic that decides what a robot
 * does. A wrong offset here is a motion nobody chose, and it would reach the body wearing
 * the operator's name.
 */

import type { Action, Intent } from "../api/types";

/** How much one press moves a joint [rad]. A nudge, not a lunge. */
export const STEP_RAD = 0.02;

/**
 * Apply per-joint offsets across the whole chunk.
 *
 * The offset is applied to every step rather than to the first. An operator saying "a bit
 * higher" means the whole approach, not just its start — correcting one step and leaving
 * the rest would produce a kink the person never asked for.
 *
 * Returns a complete `Intent`. The runtime never receives a delta: it would have to
 * reconstruct what the delta was relative to, and a reconstruction that is even slightly
 * wrong is a motion nobody chose. Doing the arithmetic here means the operator sees the
 * resulting numbers before committing.
 */
export function applyOffsets(intent: Intent, offsets: number[]): Intent {
  return {
    ...intent,
    issued_at: new Date().toISOString(),
    actions: intent.actions.map(
      (action): Action => ({
        ...action,
        values: action.values.map((value, index) => value + (offsets[index] ?? 0)),
      }),
    ),
    goal: "operator correction",
  };
}

/** Whether anything was actually changed. */
export function isTouched(offsets: number[]): boolean {
  return offsets.some((value) => value !== 0);
}

/**
 * Move one joint by one step.
 *
 * Returns a new array rather than mutating: the editor keeps this in React state, and a
 * mutated array would not re-render — the operator would press a button and see nothing
 * change, which is indistinguishable from a broken interface.
 */
export function nudge(offsets: number[], joint: number, direction: 1 | -1): number[] {
  return offsets.map((value, index) =>
    index === joint ? value + direction * STEP_RAD : value,
  );
}

/** How many joints an intent commands, for sizing the editor. */
export function jointCount(intent: Intent): number {
  return intent.actions[0]?.values.length ?? 0;
}
