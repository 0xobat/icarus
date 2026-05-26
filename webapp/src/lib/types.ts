/**
 * TypeScript shapes mirroring the Postgres rows the webapp reads.
 *
 * Single source of truth for column names lives in
 * `lib/src/icarus/db/models.py` (SQLAlchemy ORM). These interfaces snapshot
 * the subset of columns the read-only insight pages need; if a Python-side
 * column rename ships, the corresponding interface here must move in lockstep
 * (parallel-artifacts-drift discipline).
 *
 * Numerics are read back from Postgres as strings by `pg` (to preserve
 * arbitrary precision). We keep them typed as `string` here and let each page
 * decide whether to format them via `parseFloat`. Timestamps come back as JS
 * `Date` objects from the driver's default parser.
 */

export type CandidateState =
  | "backtest"
  | "paper_trade"
  | "live_capped"
  | "live_mature"
  | "demoted_paper"
  | "archived";

export type JudgeVerdict = "PASS" | "FLAG_FOR_OPERATOR" | "REJECT";

export type DiscordReplyTokenStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired";

export type DiscordReplyTokenKind = "promotion_request" | "demotion_explain";

export interface LakeRosterRow {
  candidate_id: string;
  template_id: string;
  state: CandidateState;
  allocation_usd: string;
  allocation_max_pct: string;
  last_transition_at: Date;
  breaker_tripped: boolean;
}

export interface TemplateRow {
  template_id: string;
  title: string;
  chain: string;
  protocol: string;
  judge_verdict: JudgeVerdict;
  judge_rationale: string | null;
  created_at: Date;
}

export interface DecisionAuditRow {
  id: number;
  timestamp: Date;
  decision_action: string;
  reasoning: string | null;
  passed_verification: boolean;
  prompt_tokens: number | null;
}

export interface PromotionRequestRow {
  id: number;
  candidate_id: string;
  template_id: string;
  kind: DiscordReplyTokenKind;
  status: DiscordReplyTokenStatus;
  created_at: Date;
  expires_at: Date;
}
