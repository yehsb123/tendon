/**
 * The live channel.
 *
 * Mirrors the message contract documented in `src/tendon/api/ws.py`. The socket carries
 * only what must be live; anything precomputable is REST.
 *
 * The rule that shapes this file: losing the connection must never leave the body
 * mid-motion, and must never leave the operator unable to tell a frozen robot from a
 * frozen UI. Someone who cannot tell those apart reaches for the physical stop, which
 * destroys the context the whole interrupt design exists to preserve. So connection state
 * is surfaced, never hidden behind a spinner.
 */

import type {
  Confidence,
  Intent,
  InterruptContext,
  InterruptResolution,
  Observation,
  Resolution,
} from "./types";

// --------------------------------------------------------------------------- inbound

/** The action chunk about to execute. Render it; do not assume it will run. */
export interface IntentMessage {
  type: "intent";
  episode_id: string;
  intent: Intent;
}

/** Body state at the control rate, downsampled for display. */
export interface StateMessage {
  type: "state";
  episode_id: string;
  step: number;
  observation: Observation;
  confidence: Confidence;
}

/** Control has been handed over. The scene freezes at this context. */
export interface InterruptMessage {
  type: "interrupt";
  context: InterruptContext;
}

/** Someone resolved it — possibly another viewer. Keeps every shell in sync. */
export interface ResolvedMessage {
  type: "resolved";
  episode_id: string;
  step: number;
  resolution: InterruptResolution;
}

export type InboundMessage =
  | IntentMessage
  | StateMessage
  | InterruptMessage
  | ResolvedMessage;

// --------------------------------------------------------------------------- outbound

/** Let the pending intent execute unchanged. */
export interface ApproveMessage {
  type: "approve";
  episode_id: string;
  step: number;
}

/** Discard it and ask the policy for alternatives. Not a stop. */
export interface RejectMessage {
  type: "reject";
  episode_id: string;
  step: number;
  note: string | null;
}

/**
 * Replace the pending intent.
 *
 * Subject to the same safety checks as any policy action: an operator may correct a
 * policy but may not exceed a hard limit. The runtime can still refuse this.
 */
export interface CorrectMessage {
  type: "correct";
  episode_id: string;
  step: number;
  correction: Intent;
  note: string | null;
}

/** Request an interrupt without waiting for confidence to drop. */
export interface TakeoverMessage {
  type: "takeover";
  episode_id: string;
  reason: string | null;
}

export type OutboundMessage =
  | ApproveMessage
  | RejectMessage
  | CorrectMessage
  | TakeoverMessage;

// --------------------------------------------------------------------------- connection

export type ConnectionStatus =
  | "connecting"
  | "open"
  /** Socket is gone. The body holds position; it is not taking new intent. */
  | "reconnecting"
  /** Given up. The operator must be told explicitly, not shown a stale scene. */
  | "closed";

export interface SocketHandlers {
  onMessage: (message: InboundMessage) => void;
  onStatus: (status: ConnectionStatus, detail?: string) => void;
}

export interface SocketOptions {
  url: string;
  /** Backoff between reconnect attempts [ms]. */
  retryDelayMs?: number;
  maxRetries?: number;
}

/**
 * Connect to the runtime.
 *
 * Reconnects with backoff, and reports every state transition rather than retrying
 * silently. A shell that quietly reconnects is a shell that quietly shows old data.
 *
 * The pending decision is not held here — it lives in `state/pending` so that it
 * survives a reconnect and the operator returns to the same decision.
 */
export declare function connect(
  options: SocketOptions,
  handlers: SocketHandlers,
): { send: (message: OutboundMessage) => void; close: () => void };

/**
 * Resolution values the shell can send.
 *
 * `ABORTED` is intentionally absent: aborting an episode goes through REST, because it
 * ends a run rather than answering an interrupt, and mixing the two on one control is how
 * an operator ends a shift's work while meaning to reject one action.
 */
export const SHELL_RESOLUTIONS: readonly Resolution[] = [
  "approved",
  "rejected",
  "corrected",
] as unknown as readonly Resolution[];
