/**
 * Request/response surface.
 *
 * Everything precomputable lives here so the socket carries only what must be live.
 * Mirrors `src/tendon/api/` routes.
 *
 * Endpoints are declarations, not implementations. The runtime is not built yet, and
 * writing the client against a contract rather than against a running server is what
 * keeps the two from drifting into whatever happened to be convenient.
 */

import type {
  Capability,
  EpisodeMeta,
  Intent,
  InterruptContext,
  SafetyLimits,
} from "./types";

// --------------------------------------------------------------------------- episodes

export interface EpisodeQuery {
  skill?: string;
  body_id?: string;
  /** Only runs where a human took over. The most valuable slice in the store. */
  with_interrupts?: boolean;
  /** Curation score floor, 0 to 1. */
  min_score?: number;
  limit?: number;
  offset?: number;
}

export interface EpisodeDetail {
  meta: EpisodeMeta;
  /** Every handover in this run, in order. */
  interrupts: InterruptContext[];
  /** Rerun recording for the scene view. */
  recording_url: string | null;
}

// --------------------------------------------------------------------------- skills

export interface SkillSummary {
  namespace: string;
  name: string;
  version: string;
  summary: string;
  /** Set when this was produced by `tendon fork`. */
  forked_from: string | null;
  requires: Partial<Capability>;
  safety: SafetyLimits;
  /** Below this, the policy hands over. */
  confidence_threshold: number;
}

export interface EvaluationResult {
  skill: string;
  episodes: number;
  success_rate: number;
  intervention_rate: number;
  /** Failure label -> count. What actually changes what you do next. */
  failure_modes: Record<string, number>;
  evaluated_at: string;
}

// --------------------------------------------------------------------------- training

export interface TrainingRun {
  run_id: string;
  skill: string;
  episodes_used: number;
  /** Of those, how many contained a human correction. */
  corrections_used: number;
  started_at: string;
  finished_at: string | null;
  status: "queued" | "running" | "finished" | "failed";
  adapter_ref: string | null;
}

/**
 * The graph that decides the project.
 *
 * x: cumulative human corrections. y: intervention rate. If the line is flat, the loop
 * does not close, and that result is published as prominently as a success would be.
 * See docs/roadmap.md, v0.3.
 */
export interface InterventionCurvePoint {
  cumulative_corrections: number;
  intervention_rate: number;
  measured_at: string;
}

// --------------------------------------------------------------------------- client

export interface RestClient {
  health(): Promise<{ status: string; version: string }>;

  listEpisodes(query?: EpisodeQuery): Promise<EpisodeMeta[]>;
  getEpisode(episodeId: string): Promise<EpisodeDetail>;

  listSkills(): Promise<SkillSummary[]>;
  getSkill(ref: string): Promise<SkillSummary>;
  evaluateSkill(ref: string, episodes?: number): Promise<EvaluationResult>;

  listTrainingRuns(skill?: string): Promise<TrainingRun[]>;
  interventionCurve(skill: string): Promise<InterventionCurvePoint[]>;

  /**
   * End a run.
   *
   * Deliberately REST rather than a socket message: aborting ends an episode, which is a
   * different act from answering an interrupt. Putting them on the same control is how an
   * operator ends a shift's work while meaning to reject one action.
   */
  abortEpisode(episodeId: string, reason: string): Promise<void>;

  /** The pending intent, for a shell that connected mid-interrupt. */
  pendingIntent(episodeId: string): Promise<Intent | null>;
}

export declare function createClient(baseUrl: string): RestClient;
