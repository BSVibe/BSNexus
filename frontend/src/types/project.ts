export type ProjectStatus = 'design' | 'active' | 'paused' | 'completed'

export type PhaseStatus = 'pending' | 'active' | 'completed'

export interface Phase {
  id: string
  project_id: string
  name: string
  description: string | null
  branch_name: string
  order: number
  status: PhaseStatus
  created_at: string
  updated_at: string
}

export interface Project {
  id: string
  name: string
  description: string
  design_doc_path: string | null
  repo_path: string
  status: ProjectStatus
  llm_config: Record<string, unknown> | null
  created_at: string
  updated_at: string
  phases: Phase[]
}

export interface ProjectCreate {
  name: string
  description: string
  repo_path: string
}

export interface ProjectUpdate {
  name?: string
  description?: string
  status?: ProjectStatus
}

export interface ProjectDashboardSummary {
  id: string
  name: string
  status: ProjectStatus
  task_counts: Record<string, number>
  bug_count: number
  active_worker_count: number
  current_phase: string | null
  has_architect_session: boolean
  last_activity: string | null
}
