import apiClient from './client'

export const agentControlApi = {
  stopAll: (projectId: string) =>
    apiClient
      .post<{ project_id: string; tasks_blocked: number; queue_cancelled: number }>(
        `/api/v1/projects/${projectId}/agents/stop-all`,
      )
      .then((r) => r.data),

  restart: (projectId: string) =>
    apiClient
      .post<{ project_id: string; tasks_restarted: number }>(
        `/api/v1/projects/${projectId}/agents/restart`,
      )
      .then((r) => r.data),
}
