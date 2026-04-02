import apiClient from './client'
import type { Task, TaskCreate, TaskUpdate, TaskTransition, TransitionResponse } from '../types/task'

export const tasksApi = {
  list: (projectId: string) => apiClient.get<Task[]>(`/api/v1/tasks/by-project/${projectId}`).then(r => r.data),
  get: (id: string) => apiClient.get<Task>(`/api/v1/tasks/${id}`).then(r => r.data),
  create: (data: TaskCreate) => apiClient.post<Task>('/api/v1/tasks', data).then(r => r.data),
  update: (id: string, data: TaskUpdate) => apiClient.patch<Task>(`/api/v1/tasks/${id}`, data).then(r => r.data),
  transition: (id: string, data: TaskTransition) => apiClient.post<TransitionResponse>(`/api/v1/tasks/${id}/transition`, data).then(r => r.data),
}
