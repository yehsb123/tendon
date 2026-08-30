/**
 * What the shell knows about the running episode.
 *
 * Three concerns kept apart on purpose:
 *
 *   connection  is the runtime reachable, and is what we are showing live
 *   episode     what has happened so far
 *   pending     the decision an operator is being asked for
 *
 * `pending` is separate because it outlives a reconnect. Someone mid-decision when the
 * socket drops must come back to the same decision, not to an empty screen.
 */

import { create } from "zustand";

import {
  api,
  connect,
  type Body,
  type ConnectionStatus,
  type SessionSnapshot,
  type SocketHandle,
} from "../api/client";
import type { InboundMessage } from "../api/socket";
import type { Intent, InterruptContext, Observation } from "../api/types";

interface SessionStore {
  // connection
  status: ConnectionStatus;
  statusDetail: string | null;
  runtimeVersion: string | null;
  /** Bodies the runtime can open. Physical ones are marked and warned about. */
  bodies: Body[];

  // episode
  session: SessionSnapshot | null;
  step: number;
  observation: Observation | null;
  /** What the policy is about to do. Null between chunks. */
  intent: Intent | null;

  // pending decision
  pending: InterruptContext | null;
  deciding: boolean;
  decisionError: string | null;

  checkRuntime: () => Promise<void>;
  start: (skill: string, body: string) => Promise<void>;
  decide: (resolution: string, correction?: Intent, note?: string) => Promise<void>;
  /** Whether the correction editor is open. Separate from `pending`: an operator can
   *  open it, think, and close it again without answering the interrupt. */
  correcting: boolean;
  setCorrecting: (open: boolean) => void;
  disconnect: () => void;
}

let socket: SocketHandle | null = null;

export const useSession = create<SessionStore>((set, get) => ({
  status: "closed",
  statusDetail: null,
  runtimeVersion: null,
  bodies: [],

  session: null,
  step: 0,
  observation: null,
  intent: null,

  pending: null,
  deciding: false,
  decisionError: null,
  correcting: false,

  setCorrecting(open) {
    set({ correcting: open, decisionError: null });
  },

  async checkRuntime() {
    const result = await api.health();
    if (result.ok) {
      set({ runtimeVersion: result.value.version, statusDetail: null });
      const bodies = await api.bodies();
      if (bodies.ok) set({ bodies: bodies.value });
    } else {
      // Not connected is a state to display, not an error to throw. The panel says so.
      set({ runtimeVersion: null, status: "closed", statusDetail: result.error });
    }
  },

  async start(skill, body) {
    set({ decisionError: null });
    const result = await api.startSession(skill, body);
    if (!result.ok) {
      set({ status: "closed", statusDetail: result.error });
      return;
    }

    const snapshot = result.value;
    set({ session: snapshot, step: 0, intent: null, pending: null });

    socket?.close();
    socket = connect(
      snapshot.session_id,
      (message) => applyMessage(message, set, get),
      (status, detail) => set({ status, statusDetail: detail ?? null }),
    );
  },

  async decide(resolution, correction, note) {
    const session = get().session;
    if (session === null) return;

    set({ deciding: true, decisionError: null });
    const result = await api.decide(session.session_id, resolution, correction, note);
    set({ deciding: false });

    if (!result.ok) {
      // Surfaced rather than swallowed. An operator who thinks their correction was
      // applied when it was refused is worse off than one who is told.
      set({ decisionError: result.error });
      return;
    }

    // The runtime clears `pending` when it resumes; clearing here too keeps the controls
    // from being clickable twice while that round trip completes.
    set({ pending: null, correcting: false });
  },

  disconnect() {
    socket?.close();
    socket = null;
    set({ status: "closed", pending: null, intent: null });
  },
}));

function applyMessage(
  message: InboundMessage,
  set: (partial: Partial<SessionStore>) => void,
  get: () => SessionStore,
): void {
  switch (message.type) {
    case "intent":
      set({ intent: message.intent, step: message.step ?? get().step });
      break;

    case "state":
      set({ step: message.step, observation: message.observation });
      break;

    case "interrupt":
      // A new handover closes any editor left open from the previous one: its offsets
      // were relative to a chunk that is no longer the one being asked about.
      set({ correcting: false });
      // The scene freezes here: the context carries the observation the decision is being
      // made against, not a live feed. Deciding against a moving picture is deciding about
      // a situation that has already passed.
      set({ pending: message.context, intent: message.context.intent });
      break;

    case "resolved":
      set({ pending: null });
      break;

    case "finished": {
      const current = get().session;
      // Ignored until now, so an episode that had ended still looked like one that was
      // running: the step counter froze, no interrupt ever arrived, and nothing said why.
      // A stopped robot that the screen shows as working is the worst kind of stale.
      set({
        session: current === null
          ? null
          : { ...current, ...(message.state as Partial<SessionSnapshot>), finished: true, running: false },
        pending: null,
        intent: null,
      });
      break;
    }

    case "error":
      // The runtime had a specific objection. Showing it beats a view that simply stops
      // updating with no explanation.
      set({ statusDetail: message.detail, decisionError: message.detail });
      break;

    default:
      break;
  }
}
