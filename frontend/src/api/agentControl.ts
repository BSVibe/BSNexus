import apiClient from './client'

export const agentControlApi = {
  stopAll: (projectId: string) =>
    apiClient
      .post<{ project_id: string; cancelled: boolean; workers_notified: number }>(
        `/api/v1/projects/${projectId}/agents/stop-all`
      )
      .then((r) => r.data),
}
