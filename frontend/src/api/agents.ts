import apiClient from './client'
import type { Agent, AgentCreate, AgentOrgChartNode, AgentUpdate } from '../types/agent'

export const agentsApi = {
  list: (projectId?: string, activeOnly = true) =>
    apiClient.get<Agent[]>('/api/v1/agents', {
      params: { active_only: activeOnly, ...(projectId ? { project_id: projectId } : {}) },
    }).then((r) => r.data),

  get: (id: string) => apiClient.get<Agent>(`/api/v1/agents/${id}`).then((r) => r.data),

  create: (data: AgentCreate) => apiClient.post<Agent>('/api/v1/agents', data).then((r) => r.data),

  update: (id: string, data: AgentUpdate) =>
    apiClient.patch<Agent>(`/api/v1/agents/${id}`, data).then((r) => r.data),

  delete: (id: string) => apiClient.delete(`/api/v1/agents/${id}`),

  getOrgChart: () =>
    apiClient.get<AgentOrgChartNode[]>('/api/v1/agents/org-chart').then((r) => r.data),
}
