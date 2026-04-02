import apiClient from './client'
import type { AddTaskRequest, AddTaskResponse } from '../types/architect'

export const pmApi = {
  addTask: (projectId: string, data: AddTaskRequest) =>
    apiClient.post<AddTaskResponse>(`/api/v1/architect/add-task/${projectId}`, data).then((r) => r.data),
  start: (projectId: string) =>
    apiClient.post(`/api/v1/pm/${projectId}/start`).then((r) => r.data),
  pause: (projectId: string) =>
    apiClient.post(`/api/v1/pm/${projectId}/pause`).then((r) => r.data),
  status: (projectId: string) =>
    apiClient.get(`/api/v1/pm/${projectId}/status`).then((r) => r.data),
  queueNext: (projectId: string) =>
    apiClient.post(`/api/v1/pm/${projectId}/queue-next`).then((r) => r.data),
}
