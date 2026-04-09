export type AgentStatus = 'online' | 'busy' | 'offline' | 'budget_exceeded'
export type ExecutorType = 'claude_code' | 'claude_api' | 'bsgateway' | 'codex' | 'generic_llm' | 'worker'

export interface Agent {
  id: string
  tenant_id: string
  name: string
  role: string
  title: string | null
  job_description: string | null
  executor_config_id: string | null
  executor_type: ExecutorType
  executor_config: Record<string, unknown>
  system_prompt: string | null
  skills: string[] | null
  capabilities: string[]
  parent_agent_id: string | null
  heartbeat_interval_seconds: number | null
  heartbeat_enabled: boolean
  last_heartbeat_at: string | null
  monthly_budget_cents: number | null
  current_month_spent_cents: number
  status: AgentStatus
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface AgentOrgChartNode {
  agent: Agent
  children: AgentOrgChartNode[]
}

export interface AgentCreate {
  name: string
  role: string
  title?: string
  job_description?: string
  executor_config_id?: string | null
  executor_type?: ExecutorType
  executor_config?: Record<string, unknown>
  system_prompt?: string
  skills?: string[]
  capabilities?: string[]
  parent_agent_id?: string
  heartbeat_interval_seconds?: number
  heartbeat_enabled?: boolean
  monthly_budget_cents?: number
}

export interface AgentUpdate {
  name?: string
  role?: string
  title?: string
  job_description?: string
  executor_config_id?: string | null
  executor_type?: ExecutorType
  executor_config?: Record<string, unknown>
  system_prompt?: string
  skills?: string[]
  capabilities?: string[]
  parent_agent_id?: string | null
  heartbeat_interval_seconds?: number | null
  heartbeat_enabled?: boolean
  monthly_budget_cents?: number | null
  is_active?: boolean
}
