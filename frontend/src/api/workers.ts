import apiClient from './client'

export interface WorkerInfo {
  id: string
  tenant_id: string
  name: string
  labels: string[]
  status: string
  last_heartbeat: string | null
  capabilities: string[]
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface InstallTokenStatus {
  has_token: boolean
}

export interface InstallTokenCreated {
  has_token: true
  token: string
}

export const workersApi = {
  list: async (): Promise<WorkerInfo[]> => {
    const { data } = await apiClient.get<WorkerInfo[]>('/api/v1/workers')
    return data
  },
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/api/v1/workers/${id}`)
  },
  getInstallTokenStatus: async (): Promise<InstallTokenStatus> => {
    const { data } = await apiClient.get<InstallTokenStatus>(
      '/api/v1/workers/install-token',
    )
    return data
  },
  generateInstallToken: async (): Promise<InstallTokenCreated> => {
    const { data } = await apiClient.post<InstallTokenCreated>(
      '/api/v1/workers/install-token',
    )
    return data
  },
  revokeInstallToken: async (): Promise<void> => {
    await apiClient.delete('/api/v1/workers/install-token')
  },
}
