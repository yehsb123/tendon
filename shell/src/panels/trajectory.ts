/**
 * The arithmetic behind the trajectory drawing, separated so it can be tested.
 *
 * A drawing that is subtly wrong is worse than one that is missing: an operator reads it,
 * believes it, and approves something it does not depict. These functions are where that
 * would happen, so they live apart from the JSX rather than inside it.
 */

import type { Intent, Observation } from "../api/types";

/** Below this, a joint has not meaningfully moved [rad]. Roughly a hobby servo's step. */
export const MOTION_EPSILON = 1e-3;

/**
 * Joint values per joint over the chunk.
 *
 * Empty for Cartesian action spaces. Drawing an end-effector pose as though it were joint
 * angles would produce a plausible-looking picture of something that is not happening,
 * which is the specific failure this whole panel is supposed to avoid.
 */
export function toSeries(intent: Intent): number[][] {
  const first = intent.actions[0];
  if (first === undefined) return [];
  if (first.space !== "joint_position" && first.space !== "joint_velocity") return [];

  const width = first.values.length;
  return Array.from({ length: width }, (_, joint) =>
    intent.actions.map((action) => action.values[joint] ?? 0),
  );
}

export interface Busiest {
  joint: number;
  /** How far that joint travels over the chunk [rad]. */
  travel: number;
}

/**
 * Which joint is doing the work.
 *
 * The question a person asks first, and the reason the panel does not draw every axis with
 * equal weight: five equally-weighted curves over ten steps is accurate and unreadable.
 */
export function busiest(series: number[][]): Busiest {
  if (series.length === 0) return { joint: 0, travel: 0 };

  const travels = series.map((values) =>
    values.length === 0 ? 0 : Math.max(...values) - Math.min(...values),
  );
  let index = 0;
  for (let i = 1; i < travels.length; i += 1) {
    if ((travels[i] ?? 0) > (travels[index] ?? 0)) index = i;
  }
  return { joint: index, travel: travels[index] ?? 0 };
}

export interface Scale {
  low: number;
  high: number;
  span: number;
}

/**
 * One vertical scale across every joint, including where the arm is now.
 *
 * Scaling per joint would make a 0.002 rad wobble look exactly like a 0.4 rad sweep. That
 * is the single most misleading thing this panel could do, so the scale is shared and the
 * span is floored rather than allowed to collapse when nothing moves.
 */
export function scaleFor(series: number[][], observation: Observation | null): Scale {
  const values = series.flat();
  const start = observation?.proprio.joint_positions ?? [];
  const all = [...values, ...start];

  if (all.length === 0) return { low: 0, high: 0, span: MOTION_EPSILON };

  const low = Math.min(...all);
  const high = Math.max(...all);
  return { low, high, span: Math.max(high - low, MOTION_EPSILON) };
}

/** Describe the plan in words, because a shape alone does not say how far or how fast. */
export function caption(travel: number, horizonSeconds: number, joint: number): string {
  if (travel < MOTION_EPSILON) return "holding position";
  return `J${joint} moves ${travel.toFixed(3)} rad over ${horizonSeconds.toFixed(2)} s`;
}
