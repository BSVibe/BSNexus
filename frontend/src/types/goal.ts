export type GoalLevel = 'mission' | 'department' | 'project' | 'task'

export interface Goal {
  id: string
  tenant_id: string
  parent_goal_id: string | null
  level: GoalLevel
  title: string
  description: string | null
  project_id: string | null
  agent_id: string | null
  created_at: string
  updated_at: string
}

export interface GoalCreate {
  title: string
  description?: string
  level?: GoalLevel
  parent_goal_id?: string | null
  project_id?: string | null
  agent_id?: string | null
}

export interface GoalUpdate {
  title?: string
  description?: string
  level?: GoalLevel
  parent_goal_id?: string | null
}

export interface GoalAncestry {
  chain: Goal[]
}
