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
  type Compatibility,
  type ConnectionStatus,
  type SessionSnapshot,
  type SocketHandle,
} from "../api/client";
import type { SkillDetail, SkillSummary } from "../api/client";
import type { InboundMessage } from "../api/socket";
import type { Intent, InterruptContext, Observation } from "../api/types";

interface SessionStore {
  // connection
  status: ConnectionStatus;
  statusDetail: string | null;
  runtimeVersion: string | null;
  /** Bodies the runtime can open. Physical ones are marked and warned about. */
  bodies: Body[];
  /** Skills found under the runtime's skill root. */
  skills: SkillSummary[];
  /** What the operator has chosen to run, and on what. */
  chosenSkill: string | null;
  chosenBody: string | null;
  /** Whether that pairing can run, and every reason it cannot. Null until asked. */
  compatibility: Compatibility | null;
  /**
   * The chosen skill's detail, fetched when it is chosen.
   *
   * Carried for `capped`: whether this machine's `~/.tendon/limits.yaml` narrowed the
   * bounds the skill asked for. `Skills` shows it on a page somebody visits to read about
   * a skill; `Live` is where they start one, and that is the screen where the number in
   * force matters.
   */
  chosenDetail: SkillDetail | null;

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
  /**
   * How the last interrupt was answered, and at which step.
   *
   * Null until one is. Kept because the `resolved` message exists for the case where
   * *somebody else* answered — and the shell was clearing the question without recording
   * the answer, so a second viewer saw the controls vanish and never learned what had been
   * decided. Approve and reject are opposite instructions to a robot.
   */
  lastResolution: { step: number; resolution: string } | null;

  checkRuntime: () => Promise<void>;
  choose: (skill: string | null, body: string | null) => Promise<void>;
  start: (skill: string, body: string, policy?: string) => Promise<void>;
  /**
   * Which policy the next run uses: `scripted` or `adapter`.
   *
   * Kept in the store rather than passed at the call site because the view needs to show
   * which one is selected, and because the answer decides what the operator is actually
   * supervising — a synthetic sweep with a placed uncertain region, or a trained model.
   */
  chosenPolicy: string;
  choosePolicy: (policy: string) => void;
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
  skills: [],
  chosenSkill: null,
  chosenBody: null,
  compatibility: null,
  chosenDetail: null,
  // Scripted by default: it needs no weights, no GPU and no training run, so the shell
  // still opens and demonstrates the loop on a clean checkout.
  chosenPolicy: "scripted",

  session: null,
  step: 0,
  observation: null,
  intent: null,

  pending: null,
  deciding: false,
  decisionError: null,
  lastResolution: null,
  correcting: false,

  setCorrecting(open) {
    set({ correcting: open, decisionError: null });
  },

  async checkRuntime() {
    const result = await api.health();
    if (result.ok) {
      set({ runtimeVersion: result.value.version, statusDetail: null });
      const [bodies, skills] = await Promise.all([api.bodies(), api.skills()]);
      if (bodies.ok) set({ bodies: bodies.value });
      if (skills.ok) set({ skills: skills.value });

      // Pick a default only when there is no ambiguity. Choosing one of several on the
      // operator's behalf is how someone ends up running a skill they did not select.
      const usable = bodies.ok ? bodies.value.filter((b) => b.available && b.simulated) : [];
      const runnable = skills.ok ? skills.value.filter((s) => !s.error) : [];
      if (usable.length === 1 && runnable.length === 1) {
        await get().choose(runnable[0]?.ref ?? null, usable[0]?.name ?? null);
      }
    } else {
      // Not connected is a state to display, not an error to throw. The panel says so.
      set({ runtimeVersion: null, status: "closed", statusDetail: result.error });
    }
  },

  async choose(skill, body) {
    set({ chosenSkill: skill, chosenBody: body, compatibility: null, chosenDetail: null });
    if (skill === null || body === null) return;

    // Split here rather than in the request, so a ref that is not `namespace/name`
    // fails visibly instead of producing a URL that happens to route somewhere.
    const [namespace, name] = skill.split("/");
    if (!namespace || !name) {
      set({ statusDetail: `skill ref ${skill} is not namespace/name` });
      return;
    }

    const result = await api.compatibility(namespace, name, body);
    // A failed check is not the same as an incompatible pairing. Treating it as
    // incompatible would hide a runtime problem behind a wrong explanation.
    set({ compatibility: result.ok ? result.value : null });
    if (!result.ok) set({ statusDetail: result.error });

    // The skill's own detail, for one fact that belongs on the screen where somebody
    // presses start: whether this machine's ceiling narrowed the limits. `tendon run`
    // prints "local limits are tighter than the skill's" on every run and the shell said
    // nothing, so the same fact was disclosed on one surface and withheld on the other —
    // and the withheld one is the operator's.
    //
    // Null on failure rather than stale: a cap notice left over from another skill is
    // worse than no notice, because it is about the wrong motion.
    const detail = await api.skill(namespace, name);
    set({ chosenDetail: detail.ok ? detail.value : null });
  },

  choosePolicy(policy) {
    set({ chosenPolicy: policy });
  },

  async start(skill, body, policy) {
    set({ decisionError: null });
    const result = await api.startSession(skill, body, policy ?? get().chosenPolicy);
    if (!result.ok) {
      set({ status: "closed", statusDetail: result.error });
      return;
    }

    const snapshot = result.value;
    // `lastResolution` too: it belongs to the episode that produced it, and carrying it
    // into the next one would show a decision nobody made about what is on screen now.
    set({ session: snapshot, step: 0, intent: null, pending: null, lastResolution: null });

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
      set({
        pending: null,
        lastResolution: { step: message.step, resolution: message.resolution },
      });
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
