import apiClient from './client'

export interface ChatMessageOut {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  actions: Array<{ type: string; task_id?: string; title?: string }>
}

export interface ChatResponse {
  message: ChatMessageOut
}

export interface ChatHistoryResponse {
  messages: ChatMessageOut[]
}

export const agentChatApi = {
  send: (projectId: string, agentId: string, message: string) =>
    apiClient
      .post<ChatResponse>(`/api/v1/projects/${projectId}/chat`, { agent_id: agentId, message })
      .then((r) => r.data),

  history: (projectId: string, agentId: string) =>
    apiClient
      .get<ChatHistoryResponse>(`/api/v1/projects/${projectId}/chat`, { params: { agent_id: agentId } })
      .then((r) => r.data),

  clear: (projectId: string, agentId: string) =>
    apiClient
      .delete(`/api/v1/projects/${projectId}/chat`, { params: { agent_id: agentId } })
      .then((r) => r.data),
}
