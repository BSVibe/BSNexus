export type TaskStatus = 'waiting' | 'ready' | 'in_progress' | 'review' | 'done' | 'redesign'
export type TaskPriority = 'low' | 'medium' | 'high' | 'critical'
export type TaskType = 'feature' | 'bug' | 'improvement' | 'test' | 'chore' | 'refactor'
export type TaskSource = 'llm' | 'auto_bug' | 'manual'

export interface QAFeedbackEntry {
  type: 'qa_failure' | 'execution_failure'
  attempt: number
  feedback?: string
  error?: string
  error_category?: string
  timestamp: string
}

export interface Task {
  id: string
  project_id: string
  phase_id: string
  title: string
  description: string | null
  status: TaskStatus
  priority: TaskPriority
  task_type: TaskType
  source: TaskSource
  agent_id: string | null
  parent_task_id: string | null
  worker_prompt: Record<string, unknown> | null
  qa_prompt: Record<string, unknown> | null
  branch_name: string | null
  commit_hash: string | null
  qa_result: Record<string, unknown> | null
  output_path: string | null
  error_message: string | null
  retry_count: number
  max_retries: number
  qa_feedback_history: Array<QAFeedbackEntry> | null
  version: number
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
  depends_on: string[]
}

export interface TaskCreate {
  project_id: string
  phase_id: string
  title: string
  description?: string
  priority?: TaskPriority
  task_type?: TaskType
  agent_id?: string
  source?: TaskSource
  depends_on?: string[]
  worker_prompt?: string
  qa_prompt?: string
}

export interface TaskUpdate {
  title?: string
  description?: string
  priority?: TaskPriority
  expected_version?: number
}

export interface TaskTransition {
  new_status: TaskStatus
  reason?: string
  actor?: string
  expected_version?: number
}

export interface TransitionResponse {
  task_id: string
  status: TaskStatus
  previous_status: TaskStatus
  transition: Record<string, unknown>
}

export interface BoardColumn {
  tasks: Task[]
}

export interface PhaseInfo {
  name: string
  order: number
  status: 'pending' | 'active' | 'completed'
}

export interface BoardResponse {
  project_id: string
  columns: Record<string, BoardColumn>
  stats: Record<string, number>
  phases: Record<string, PhaseInfo>
  redesign_tasks: Task[]
}
