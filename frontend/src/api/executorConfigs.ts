import apiClient from './client'
import type { ExecutorConfig, ExecutorConfigCreate, ExecutorConfigUpdate } from '../types/executor'

export const executorConfigsApi = {
  list: () =>
    apiClient.get<ExecutorConfig[]>('/api/v1/executor-configs').then((r) => r.data),

  get: (id: string) =>
    apiClient.get<ExecutorConfig>(`/api/v1/executor-configs/${id}`).then((r) => r.data),

  create: (data: ExecutorConfigCreate) =>
    apiClient.post<ExecutorConfig>('/api/v1/executor-configs', data).then((r) => r.data),

  update: (id: string, data: ExecutorConfigUpdate) =>
    apiClient.patch<ExecutorConfig>(`/api/v1/executor-configs/${id}`, data).then((r) => r.data),

  delete: (id: string) =>
    apiClient.delete(`/api/v1/executor-configs/${id}`),
}
