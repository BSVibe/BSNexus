import apiClient from './client'

export type TaskStatus = 'pending' | 'running' | 'blocked' | 'done'
export type TaskPriority = 'low' | 'medium' | 'high' | 'critical'
export type TaskType = 'feature' | 'bug' | 'improvement' | 'test' | 'chore' | 'refactor'
export type PhaseStatus = 'pending' | 'active' | 'completed'
export type ProjectStatus = 'design' | 'active' | 'paused' | 'completed'
export type AgentDot = 'green' | 'yellow' | 'red' | 'gray' | 'blue'

export interface PlanTaskNode {
  id: string
  title: string
  status: TaskStatus
  priority: TaskPriority
  task_type: TaskType
  agent_id: string | null
  agent_name: string | null
  depends_on_ids: string[]
  started_at: string | null
  completed_at: string | null
}

export interface PlanPhaseNode {
  id: string
  name: string
  description: string | null
  status: PhaseStatus
  order: number
  tasks: PlanTaskNode[]
}

export interface PlanTreeResponse {
  project_id: string
  project_name: string
  project_status: ProjectStatus
  goal: string | null
  phases: PlanPhaseNode[]
}

export interface AgentStatusCard {
  agent_id: string
  name: string
  role: string
  title: string | null
  dot: AgentDot
  current_task: { id: string; title: string; status: TaskStatus } | null
}

export interface ActivityEntry {
  id: string
  task_id: string
  level: 'milestone' | 'tool'
  event_type: string
  summary: string
  detail: Record<string, unknown> | null
  created_at: string
}

export interface ActivityFeedResponse {
  task_id: string
  entries: ActivityEntry[]
}

export const planTreeApi = {
  getTree: (projectId: string) =>
    apiClient.get<PlanTreeResponse>(`/api/v1/projects/${projectId}/plan-tree`).then((r) => r.data),

  getAgentStatus: (projectId: string) =>
    apiClient
      .get<AgentStatusCard[]>(`/api/v1/projects/${projectId}/agent-status`)
      .then((r) => r.data),

  getTaskActivity: (taskId: string, level: 'milestone' | 'all' = 'milestone') =>
    apiClient
      .get<ActivityFeedResponse>(`/api/v1/tasks/${taskId}/activity`, { params: { level } })
      .then((r) => r.data),
}
