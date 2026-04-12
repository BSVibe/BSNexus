import apiClient from './client'

export interface ChatMessageOut {
  id: string
  role: 'user' | 'assistant'
  content: string
  agent_id: string | null
  agent_name: string | null
  task_id: string | null
  created_at: string
  actions: Array<{ type: string; task_id?: string; goal_id?: string; title?: string }>
}

export interface ChatDispatchResponse {
  dispatched_agents: string[]
}

export interface ChatHistoryResponse {
  messages: ChatMessageOut[]
}

export const agentChatApi = {
  send: (projectId: string, message: string) =>
    apiClient
      .post<ChatDispatchResponse>(`/api/v1/projects/${projectId}/chat`, { message })
      .then((r) => r.data),

  history: (projectId: string) =>
    apiClient
      .get<ChatHistoryResponse>(`/api/v1/projects/${projectId}/chat`)
      .then((r) => r.data),

  clear: (projectId: string) =>
    apiClient
      .delete(`/api/v1/projects/${projectId}/chat`)
      .then((r) => r.data),
}
