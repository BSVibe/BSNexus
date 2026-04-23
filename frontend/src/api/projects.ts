import apiClient from './client'

export interface Project {
  id: string
  tenant_id: string
  name: string
  description: string
  status: string
  bsage_workspace_id: string | null
  bsupervisor_policy_id: string | null
  created_at: string
  updated_at: string
}

export interface ProjectCreate {
  name: string
  description?: string
  bsage_workspace_id?: string | null
  bsupervisor_policy_id?: string | null
}

export interface ProjectUpdate {
  name?: string
  description?: string
  status?: string
  bsage_workspace_id?: string | null
  bsupervisor_policy_id?: string | null
}

export const projectsApi = {
  list: async (): Promise<Project[]> => {
    const { data } = await apiClient.get<Project[]>('/api/v1/projects')
    return data
  },
  get: async (id: string): Promise<Project> => {
    const { data } = await apiClient.get<Project>(`/api/v1/projects/${id}`)
    return data
  },
  create: async (body: ProjectCreate): Promise<Project> => {
    const { data } = await apiClient.post<Project>('/api/v1/projects', body)
    return data
  },
  update: async (id: string, body: ProjectUpdate): Promise<Project> => {
    const { data } = await apiClient.patch<Project>(`/api/v1/projects/${id}`, body)
    return data
  },
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/api/v1/projects/${id}`)
  },
}
