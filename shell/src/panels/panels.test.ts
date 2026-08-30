/**
 * The arithmetic an operator's decision rests on.
 *
 * The shell had no tests while accumulating two pieces of maths that decide what a person
 * sees and what a robot does: how a plan is drawn, and how a correction is built. A drawing
 * that is subtly wrong is worse than one that is missing — someone reads it, believes it,
 * and approves something it does not depict.
 */

import { describe, expect, it } from "vitest";

import type { Action, Intent, Observation } from "../api/types";
import { ActionSpace, ConfidenceSource } from "../api/types";
import { applyOffsets, isTouched, jointCount, nudge, STEP_RAD } from "./correction";
import { busiest, caption, MOTION_EPSILON, scaleFor, toSeries } from "./trajectory";

function action(values: number[], space: ActionSpace = ActionSpace.JOINT_POSITION): Action {
  return { space, values, gripper: null };
}

function intent(actions: Action[], horizon = 0.5): Intent {
  return {
    issued_at: "2026-08-31T00:00:00Z",
    horizon_s: horizon,
    actions,
    confidence: { score: 0.9, source: ConfidenceSource.CHUNK_VARIANCE, reasons: [] },
    goal: null,
    target: null,
  };
}

function observation(joints: number[]): Observation {
  return {
    t: "2026-08-31T00:00:00Z",
    step: 0,
    proprio: {
      joint_positions: joints,
      joint_velocities: null,
      gripper_open: null,
      force: null,
    },
    frames: {},
    extra: {},
  };
}

// ------------------------------------------------------------------------- trajectory

describe("toSeries", () => {
  it("transposes a chunk into one series per joint", () => {
    const series = toSeries(intent([action([0, 10]), action([1, 11]), action([2, 12])]));
    expect(series).toEqual([
      [0, 1, 2],
      [10, 11, 12],
    ]);
  });

  it("draws nothing for a Cartesian action space", () => {
    // Drawing an end-effector pose as though it were joint angles would produce a
    // plausible-looking picture of something that is not happening.
    const series = toSeries(intent([action([0, 0, 0, 0, 0, 0], ActionSpace.EE_ABS_POSE)]));
    expect(series).toEqual([]);
  });

  it("handles an empty chunk without throwing", () => {
    expect(toSeries(intent([]))).toEqual([]);
  });
});

describe("busiest", () => {
  it("finds the joint that travels furthest", () => {
    const result = busiest([
      [0, 0.01, 0.02],
      [0, 0.4, 0.8],
    ]);
    expect(result.joint).toBe(1);
    expect(result.travel).toBeCloseTo(0.8);
  });

  it("reports no travel when nothing moves", () => {
    expect(busiest([[0.1, 0.1, 0.1]]).travel).toBe(0);
  });

  it("survives an empty series", () => {
    expect(busiest([])).toEqual({ joint: 0, travel: 0 });
  });
});

describe("scaleFor", () => {
  it("shares one scale across every joint", () => {
    // Per-joint scaling would make a 0.002 rad wobble look exactly like a 0.4 rad sweep,
    // which is the single most misleading thing the panel could do.
    const scale = scaleFor(
      [
        [0, 0.002],
        [0, 0.4],
      ],
      null,
    );
    expect(scale.low).toBe(0);
    expect(scale.high).toBeCloseTo(0.4);
  });

  it("includes where the arm is now", () => {
    // A plan drawn without the current position says where the arm will go and not whether
    // that is an adjustment or a lunge. One joint here, so the current position is the
    // only thing that can widen the scale.
    const scale = scaleFor([[0.5, 0.6]], observation([0.1]));
    expect(scale.low).toBeCloseTo(0.1);
    expect(scale.high).toBeCloseTo(0.6);
  });

  it("floors the span so a still arm does not divide by zero", () => {
    const scale = scaleFor([[0.2, 0.2]], null);
    expect(scale.span).toBe(MOTION_EPSILON);
  });

  it("survives having nothing at all", () => {
    expect(scaleFor([], null).span).toBe(MOTION_EPSILON);
  });
});

describe("caption", () => {
  it("says holding position when nothing moves", () => {
    expect(caption(0, 0.5, 0)).toBe("holding position");
  });

  it("names the joint, the distance and the time", () => {
    // The shape alone does not say how far or how fast, and both change the decision.
    expect(caption(0.25, 0.5, 2)).toBe("J2 moves 0.250 rad over 0.50 s");
  });
});

// ------------------------------------------------------------------------- correction

describe("applyOffsets", () => {
  it("applies the offset to every step, not just the first", () => {
    // "A bit higher" means the whole approach. Correcting one step would produce a kink
    // the operator never asked for.
    const corrected = applyOffsets(intent([action([0, 5]), action([1, 6])]), [0.1, 0]);
    expect(corrected.actions.map((a) => a.values)).toEqual([
      [0.1, 5],
      [1.1, 6],
    ]);
  });

  it("leaves joints with no offset untouched", () => {
    const corrected = applyOffsets(intent([action([1, 2, 3])]), [0, 0.5, 0]);
    expect(corrected.actions[0]?.values).toEqual([1, 2.5, 3]);
  });

  it("returns a whole intent, never a delta", () => {
    // The runtime would have to reconstruct what a delta was relative to, and a slightly
    // wrong reconstruction is a motion nobody chose.
    const original = intent([action([1, 2])], 0.25);
    const corrected = applyOffsets(original, [0.1, 0.1]);

    expect(corrected.horizon_s).toBe(0.25);
    expect(corrected.actions).toHaveLength(1);
    expect(corrected.goal).toBe("operator correction");
  });

  it("does not mutate the intent it was given", () => {
    const original = intent([action([1, 2])]);
    applyOffsets(original, [9, 9]);
    expect(original.actions[0]?.values).toEqual([1, 2]);
  });
});

describe("nudge", () => {
  it("moves one joint by one step", () => {
    expect(nudge([0, 0, 0], 1, 1)).toEqual([0, STEP_RAD, 0]);
    expect(nudge([0, 0, 0], 1, -1)).toEqual([0, -STEP_RAD, 0]);
  });

  it("returns a new array so React re-renders", () => {
    // A mutated array would leave the operator pressing a button and seeing nothing
    // change, which is indistinguishable from a broken interface.
    const before = [0, 0];
    const after = nudge(before, 0, 1);
    expect(after).not.toBe(before);
    expect(before).toEqual([0, 0]);
  });

  it("accumulates across presses", () => {
    let offsets = [0, 0];
    offsets = nudge(offsets, 0, 1);
    offsets = nudge(offsets, 0, 1);
    expect(offsets[0]).toBeCloseTo(2 * STEP_RAD);
  });
});

describe("isTouched", () => {
  it("is false until something changes", () => {
    // An unchanged correction is an approval wearing the wrong label, and it would be
    // stored as a lesson that teaches nothing.
    expect(isTouched([0, 0, 0])).toBe(false);
    expect(isTouched(nudge([0, 0, 0], 2, -1))).toBe(true);
  });
});

describe("jointCount", () => {
  it("sizes the editor from the intent", () => {
    expect(jointCount(intent([action([1, 2, 3])]))).toBe(3);
  });

  it("is zero for an empty chunk rather than throwing", () => {
    expect(jointCount(intent([]))).toBe(0);
  });
});
