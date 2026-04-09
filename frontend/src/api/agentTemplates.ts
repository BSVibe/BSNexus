import apiClient from './client'
import type { Agent } from '../types/agent'

export interface AgentTemplate {
  name: string
  role: string
  title: string
  job_description: string
  executor_type: string
  capabilities: string[]
  children: AgentTemplate[]
}

export interface OrgTemplate {
  id: string
  name: string
  description: string
  agent_count: number
  agents: AgentTemplate[]
}

export const agentTemplatesApi = {
  list: () =>
    apiClient.get<OrgTemplate[]>('/api/v1/agent-templates').then((r) => r.data),

  apply: (templateId: string) =>
    apiClient.post<Agent[]>(`/api/v1/agent-templates/${templateId}/apply`).then((r) => r.data),
}
