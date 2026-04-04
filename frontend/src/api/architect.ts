import apiClient, { API_BASE_URL } from './client'
import { parseSSEStream } from '../utils/sse'
import type { DesignSession, CreateSessionRequest, DesignMessageResponse, FinalizeRequest, MigrateRequest, BrowseResult } from '../types/architect'
import type { Project } from '../types/project'

export interface PhaseRedesignRequest {
  llm_config?: {
    api_key: string
    model?: string
    base_url?: string
  }
}

export interface PhaseRedesignResponse {
  phase_id: string
  project_id: string
  reasoning: string
  tasks_kept: number
  tasks_deleted: number
  tasks_created: number
}

export interface StreamCallbacks {
  onChunk: (text: string) => void
  onDone: (fullText: string) => void
  onFinalizeReady: (designContext: string) => void
  onError: (message: string) => void
}

export const architectApi = {
  listSessions: (status?: string) => apiClient.get<DesignSession[]>('/api/v1/architect/sessions', { params: status ? { status } : undefined }).then(r => r.data),
  createSession: (data: CreateSessionRequest) => apiClient.post<DesignSession>('/api/v1/architect/sessions', data).then(r => r.data),
  getSession: (id: string) => apiClient.get<DesignSession>(`/api/v1/architect/sessions/${id}`).then(r => r.data),
  sendMessage: (sessionId: string, content: string) => apiClient.post<DesignMessageResponse>(`/api/v1/architect/sessions/${sessionId}/message`, { content }).then(r => r.data),
  finalize: (sessionId: string, data: FinalizeRequest) => apiClient.post<Project>(`/api/v1/architect/sessions/${sessionId}/finalize`, data).then(r => r.data),
  deleteSession: (id: string) => apiClient.delete(`/api/v1/architect/sessions/${id}`).then(r => r.data),
  batchDeleteSessions: (ids: string[]) => apiClient.post<{ deleted: number }>('/api/v1/architect/sessions/batch-delete', { ids }).then(r => r.data),
  redesignPhase: (phaseId: string, data: PhaseRedesignRequest = {}) => apiClient.post<PhaseRedesignResponse>(`/api/v1/architect/redesign/phase/${phaseId}`, data).then(r => r.data),
  getSessionByProject: (projectId: string) => apiClient.get<DesignSession>(`/api/v1/architect/sessions/by-project/${projectId}`).then(r => r.data),
  migrate: (data: MigrateRequest) => apiClient.post<Project>('/api/v1/architect/migrate', data).then(r => r.data),
  browse: (path: string = '/') => apiClient.get<BrowseResult>('/api/v1/architect/browse', { params: { path } }).then(r => r.data),

  streamMessage: (sessionId: string, content: string, callbacks: StreamCallbacks): AbortController => {
    const controller = new AbortController()
    const url = `${API_BASE_URL}/api/v1/architect/sessions/${sessionId}/message/stream`

    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const text = await response.text()
          callbacks.onError(`HTTP ${response.status}: ${text}`)
          return
        }
        await parseSSEStream(response, (event, data) => {
          switch (event) {
            case 'chunk':
              callbacks.onChunk(data)
              break
            case 'done':
              callbacks.onDone(data)
              break
            case 'finalize_ready':
              callbacks.onFinalizeReady(data)
              break
            case 'error':
              callbacks.onError(data)
              break
          }
        })
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          callbacks.onError(err.message || 'Stream failed')
        }
      })

    return controller
  },
}
