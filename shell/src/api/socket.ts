/**
 * The live channel.
 *
 * Mirrors what `src/tendon/api/app.py` actually sends. The socket carries only what must
 * be live; anything precomputable is REST.
 *
 * The rule that shapes this file: losing the connection must never leave the operator
 * unable to tell a frozen robot from a frozen UI. Someone who cannot tell those apart
 * reaches for the physical stop, which destroys the context the whole interrupt design
 * exists to preserve. So connection state is surfaced, never hidden behind a spinner.
 */

import type { Action, Intent, InterruptContext, Observation } from "./types";

// --------------------------------------------------------------------------- inbound

/** The action chunk about to execute. Render it; do not assume it will run. */
export interface IntentMessage {
  type: "intent";
  intent: Intent;
  step: number;
}

/** Body state at the control rate, downsampled for display. */
export interface StateMessage {
  type: "state";
  step: number;
  observation: Observation;
  /** What the policy asked for. */
  commanded: Action;
  /** What the body executed. These differ whenever the hardware clipped. */
  applied: Action;
  /** True when safety reduced the action before it reached the driver. */
  clamped: boolean;
}

/**
 * Control has been handed over. The scene freezes at this context.
 *
 * The observation inside is the one the decision is being made against, not a live feed:
 * deciding against a moving picture is deciding about a situation that has already passed.
 */
export interface InterruptMessage {
  type: "interrupt";
  context: InterruptContext;
}

/** Someone resolved it — possibly another viewer. Keeps every shell in sync. */
export interface ResolvedMessage {
  type: "resolved";
  step: number;
}

/** The episode ended. Carries the final snapshot so the view does not have to re-fetch. */
export interface FinishedMessage {
  type: "finished";
  state: Record<string, unknown>;
}

export interface ErrorMessage {
  type: "error";
  detail: string;
}

export type InboundMessage =
  | IntentMessage
  | StateMessage
  | InterruptMessage
  | ResolvedMessage
  | FinishedMessage
  | ErrorMessage;

// --------------------------------------------------------------------------- outbound

/**
 * Decisions travel over REST, not this socket.
 *
 * A decision has to be acknowledged — an operator needs to know whether their correction
 * was accepted or refused for breaching a limit. A fire-and-forget socket message cannot
 * say that, and "I clicked Correct and nothing happened" is the worst possible state to
 * leave someone in while a robot waits.
 *
 * The type is kept so the client has one shape to talk about, and so that moving decisions
 * onto the socket later is a change with a name.
 */
export type OutboundMessage = never;
