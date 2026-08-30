/**
 * Talking to the runtime.
 *
 * The declarations in `rest.ts` and `socket.ts` describe the contract; this implements it.
 * Kept separate so the shape can be read without the fetch plumbing around it.
 *
 * Every call reports failure as a value rather than throwing. A shell that throws on a
 * dropped connection unmounts the panel an operator is reading, and losing the view is
 * worse than seeing a stale one labelled as stale.
 */

import type { InboundMessage, OutboundMessage } from "./socket";

const BASE = "";

export interface Failure {
  ok: false;
  error: string;
}

export interface Success<T> {
  ok: true;
  value: T;
}

export type Result<T> = Success<T> | Failure;

async function request<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  try {
    const response = await fetch(`${BASE}${path}`, {
      headers: { "content-type": "application/json" },
      ...init,
    });

    if (!response.ok) {
      // FastAPI puts the reason in `detail`, and that reason is usually the whole point —
      // "skill needs 6 axes, body has 5" is what the operator has to act on.
      const body = await response.json().catch(() => ({}));
      return { ok: false, error: body.detail ?? `${response.status} ${response.statusText}` };
    }

    return { ok: true, value: (await response.json()) as T };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

// ------------------------------------------------------------------------------ types

export interface Health {
  status: string;
  version: string;
}

export interface Body {
  name: string;
  available: boolean;
  detail: string | null;
  /**
   * False when this body moves real hardware.
   *
   * The runtime defaults it to false for any driver that does not declare itself, so an
   * undeclared driver shows as physical. That is the safe direction to be wrong in.
   */
  simulated: boolean;
}

export interface SkillSummary {
  ref: string;
  version: string;
  summary: string;
  confidence_threshold: number;
  error?: string;
}

export interface SessionSnapshot {
  session_id: string;
  skill: string;
  body_id: string;
  running: boolean;
  finished: boolean;
  steps: number;
  interventions: number;
  corrections: number;
  ended: string;
  error: string | null;
  pending?: unknown | null;
}

// --------------------------------------------------------------------------- requests

export const api = {
  health: () => request<Health>("/api/health"),
  bodies: () => request<Body[]>("/api/bodies"),
  skills: () => request<SkillSummary[]>("/api/skills"),

  startSession: (skill: string, body: string, maxSteps = 500) =>
    request<SessionSnapshot>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ skill, body, max_steps: maxSteps }),
    }),

  session: (id: string) => request<SessionSnapshot>(`/api/sessions/${id}`),

  /**
   * Answer a pending interrupt.
   *
   * `correction` is a whole Intent. A delta would need the runtime to reconstruct what it
   * was relative to, and a reconstruction that is slightly wrong is a motion nobody chose.
   */
  decide: (id: string, resolution: string, correction?: unknown, note?: string) =>
    request<{ accepted: boolean }>(`/api/sessions/${id}/decide`, {
      method: "POST",
      body: JSON.stringify({ resolution, correction, note }),
    }),
};

// ----------------------------------------------------------------------------- socket

export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface SocketHandle {
  send: (message: OutboundMessage) => void;
  close: () => void;
}

/**
 * Subscribe to a session.
 *
 * Reconnects with backoff and reports every transition. A shell that quietly reconnects is
 * a shell that quietly shows old data, and an operator who cannot tell a frozen robot from
 * a frozen UI reaches for the physical stop — which destroys the context the whole
 * interrupt design exists to preserve.
 */
export function connect(
  sessionId: string,
  onMessage: (message: InboundMessage) => void,
  onStatus: (status: ConnectionStatus, detail?: string) => void,
  options: { maxRetries?: number; retryDelayMs?: number } = {},
): SocketHandle {
  const maxRetries = options.maxRetries ?? 5;
  const retryDelayMs = options.retryDelayMs ?? 500;

  let socket: WebSocket | null = null;
  let attempts = 0;
  let closedByUs = false;

  const url = () => {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${window.location.host}/ws/${sessionId}`;
  };

  const open = () => {
    onStatus(attempts === 0 ? "connecting" : "reconnecting");
    socket = new WebSocket(url());

    socket.onopen = () => {
      attempts = 0;
      onStatus("open");
    };

    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data) as InboundMessage);
      } catch {
        // A malformed frame is not a reason to tear down the view. Skipping it keeps the
        // last good state on screen, which is more useful than an empty panel.
      }
    };

    socket.onclose = () => {
      if (closedByUs) return;
      if (attempts >= maxRetries) {
        onStatus("closed", `gave up after ${attempts} attempts`);
        return;
      }
      attempts += 1;
      onStatus("reconnecting", `attempt ${attempts}`);
      window.setTimeout(open, retryDelayMs * attempts);
    };
  };

  open();

  return {
    send: (message) => socket?.send(JSON.stringify(message)),
    close: () => {
      closedByUs = true;
      socket?.close();
      onStatus("closed");
    },
  };
}
