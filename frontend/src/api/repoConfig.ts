import apiClient from './client'

export interface RepoConfigResponse {
  project_id: string
  repo_url: string
  branch: string
  has_token: boolean
}

export interface RepoConfigUpdate {
  repo_url: string
  branch: string
  token?: string | null
}

export const repoConfigApi = {
  get: async (projectId: string): Promise<RepoConfigResponse | null> => {
    const { data } = await apiClient.get<RepoConfigResponse | null>(
      '/api/v1/repo-config',
      { params: { project_id: projectId } },
    )
    return data
  },
  upsert: async (
    projectId: string,
    body: RepoConfigUpdate,
  ): Promise<RepoConfigResponse> => {
    const { data } = await apiClient.put<RepoConfigResponse>(
      '/api/v1/repo-config',
      body,
      { params: { project_id: projectId } },
    )
    return data
  },
  clear: async (projectId: string): Promise<void> => {
    await apiClient.delete('/api/v1/repo-config', {
      params: { project_id: projectId },
    })
  },
}
