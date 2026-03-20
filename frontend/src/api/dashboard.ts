import apiClient from './client'
import type { ProjectDashboardSummary } from '../types/project'

export interface DashboardStats {
  total_projects: number
  active_projects: number
  completed_projects: number
  total_tasks: number
  active_tasks: number
  in_progress_tasks: number
  done_tasks: number
  completion_rate: number
}

export const dashboardApi = {
  getStats: () => apiClient.get<DashboardStats>('/api/v1/dashboard/stats').then(r => r.data),
  getProjectsSummary: () => apiClient.get<ProjectDashboardSummary[]>('/api/v1/dashboard/projects-summary').then(r => r.data),
}
