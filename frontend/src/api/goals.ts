import apiClient from './client'
import type { Goal, GoalCreate, GoalUpdate, GoalAncestry } from '../types/goal'

export const goalsApi = {
  list: (params?: { level?: string; project_id?: string }) =>
    apiClient.get<Goal[]>('/api/v1/goals', { params }).then((r) => r.data),

  get: (id: string) =>
    apiClient.get<Goal>(`/api/v1/goals/${id}`).then((r) => r.data),

  ancestry: (id: string) =>
    apiClient.get<GoalAncestry>(`/api/v1/goals/${id}/ancestry`).then((r) => r.data),

  create: (data: GoalCreate) =>
    apiClient.post<Goal>('/api/v1/goals', data).then((r) => r.data),

  update: (id: string, data: GoalUpdate) =>
    apiClient.patch<Goal>(`/api/v1/goals/${id}`, data).then((r) => r.data),

  delete: (id: string) =>
    apiClient.delete(`/api/v1/goals/${id}`),
}
