// Types mirror backend Pydantic schemas exactly (snake_case) so there's
// no transform layer to drift. Keep fields in sync with
// backend/src/schemas/{project,conversation,integration,founder}.py.

export type RequestStatus = 'open' | 'running' | 'completed' | 'abandoned'

export interface Request {
  id: string
  tenant_id: string
  project_id: string
  origin_message_id: string | null
  intent_summary: string
  status: RequestStatus
  user_confirmed: boolean
  superseded_by_id: string | null
  composition_root_id: string | null
  created_at: string
  updated_at: string
}

export type DeliverableType = 'code' | 'doc' | 'design' | 'data' | 'url'
export type DeliverableStatus = 'draft' | 'ready' | 'delivered'
export type StorageBackend = 'git' | 'object' | 'url'

export type ProofState =
  | 'verification_missing'
  | 'verifying'
  | 'verified'
  | 'verification_failed'
  | 'human_review_required'
  | 'not_applicable'

export interface ProofRef {
  label: string
  type: string
  href: string
}

export interface Deliverable {
  id: string
  tenant_id: string
  project_id: string
  request_id: string | null
  type: DeliverableType
  title: string
  status: DeliverableStatus
  current_version_id: string | null
  created_at: string
  updated_at: string

  // Proof model (decision-locks A1, 2026-05-08).
  proof_state: ProofState
  verifier_type: string | null
  verifier_inputs: Record<string, unknown> | null
  verification_exit_code: number | null
  proof_summary: string | null
  proof_refs: ProofRef[] | null
  risk_summary: string | null
  verified_at: string | null
}

export interface DeliverableVersion {
  id: string
  deliverable_id: string
  version_int: number
  storage_backend: StorageBackend
  content_ref: Record<string, unknown>
  content_hash: string
  size_bytes: number | null
  created_by_run_id: string | null
  created_at: string
}

export interface Decision {
  id: string
  tenant_id: string
  project_id: string
  request_id: string | null
  origin_run_id: string | null
  question: string
  options: string[]
  blocking: boolean
  resolved_at: string | null
  resolution: string | null
  resolved_by: string | null
  created_at: string
}

export type RunStatus = 'pending' | 'running' | 'blocked' | 'done'
export type RunPriority = 'low' | 'medium' | 'high' | 'critical'
export type CompositionSource = 'bsage' | 'local'

export type ReplyQualityKind =
  | 'real_tool_calls'
  | 'pseudocode_in_chat'
  | 'fenced_block_only'
  | 'empty'
  | 'mixed'

export interface RoundSummary {
  round_idx: number
  content_chars: number
  tool_call_count: number
  reply_quality: ReplyQualityKind
  finish_reason: string | null
}

export type RunActivityLevel = 'milestone' | 'tool'

export interface RunActivity {
  id: string
  run_id: string
  project_id: string
  level: RunActivityLevel
  event_type: string
  summary: string
  detail: Record<string, unknown> | null
  created_at: string
}

export interface RunSummary {
  total_rounds: number
  total_tool_calls: number
  per_round: RoundSummary[]
  dominant_reply_quality: ReplyQualityKind
  did_emit_fenced_block: boolean
  files_actually_written: string[]
  failure_signals: string[]
  extra?: Record<string, unknown> | null
}

export interface ExecutionRun {
  id: string
  tenant_id: string
  project_id: string
  request_id: string
  parent_run_id: string | null
  composition_snapshot_id: string | null
  status: RunStatus
  priority: RunPriority
  output_type: string | null
  output_ref: Record<string, unknown> | null
  estimated_cost_cents: number
  actual_cost_cents: number
  branch_name: string | null
  commit_hash: string | null
  error_message: string | null
  retry_count: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  run_summary: RunSummary | null
}

export interface RunSummaryItem {
  run_id: string
  project_id: string
  status: RunStatus
  created_at: string
  completed_at: string | null
  summary: RunSummary | null
}

export interface RunSummaryAggregate {
  window_days: number
  total_runs: number
  counts: Partial<Record<ReplyQualityKind, number>>
  project_id: string | null
}

export interface CompositionSnapshot {
  id: string
  tenant_id: string
  request_id: string
  execution_run_id: string | null
  source: CompositionSource
  bsage_composition_id: string | null
  system_prompt_ref: Record<string, unknown>
  tools_allowed: string[]
  context_doc_refs: Array<{ path: string; title: string; score: number; excerpt_hash: string }>
  persona_label: string
  fit_score: number | null
  created_at: string
}

export type IntegrationProvider = 'bsage' | 'bsupervisor'
export type AuditFailMode = 'open' | 'closed'

// ── Brief (decision-locks A2) ─────────────────────────────────────

export interface BriefDeliverable {
  id: string
  project_id: string
  title: string
  type: DeliverableType
  proof_state: ProofState
  proof_summary: string | null
  verifier_type: string | null
  verified_at: string | null
  created_at: string
}

export interface BriefDecision {
  id: string
  project_id: string
  question: string
  blocking: boolean
  created_at: string
}

export interface BriefRun {
  id: string
  project_id: string
  request_id: string | null
  request_intent: string | null
  status: RunStatus
  started_at: string | null
  created_at: string
  error_message: string | null
}

export interface BriefNextHint {
  summary: string
  request_id: string | null
}

export interface BriefResponse {
  project_id: string | null
  generated_at: string
  shipped: BriefDeliverable[]
  needs_decision: BriefDecision[]
  blocked: BriefRun[]
  running: BriefRun[]
  next: BriefNextHint[]
}
