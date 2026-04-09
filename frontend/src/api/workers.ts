import apiClient from './client'

export interface WorkerInfo {
  id: string
  name: string
  labels: string[]
  status: string
  last_heartbeat: string | null
  capabilities: string[]
  created_at: string
}

export const workersApi = {
  list: () =>
    apiClient.get<WorkerInfo[]>('/api/v1/workers').then((r) => r.data),

  delete: (id: string) =>
    apiClient.delete(`/api/v1/workers/${id}`),
}
