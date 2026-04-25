import apiClient from './client'

export interface Message {
  id: string
  project_id: string
  role: 'user' | 'assistant'
  content: string
  request_id: string | null
  actions: unknown[]
  source: string
  external_id: string | null
  thread_ref: string | null
  created_at: string
}

export type MessageIntent = 'chit_chat' | 'question' | 'request' | 'modification'

export interface SendMessageResponse {
  message: Message
  intent: MessageIntent
  request_id: string | null
  request_created: boolean
  intent_summary: string | null
}

export const conversationApi = {
  list: async (projectId: string): Promise<Message[]> => {
    const { data } = await apiClient.get<Message[]>(
      `/api/v1/projects/${projectId}/messages`,
    )
    return data
  },
  send: async (projectId: string, content: string): Promise<SendMessageResponse> => {
    const { data } = await apiClient.post<SendMessageResponse>(
      `/api/v1/projects/${projectId}/messages`,
      { content },
    )
    return data
  },
}
