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
  worker_id: string | null
  branch_name: string | null
  commit_hash: string | null
  error_message: string | null
  retry_count: number
  created_at: string
  started_at: string | null
  completed_at: string | null
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
