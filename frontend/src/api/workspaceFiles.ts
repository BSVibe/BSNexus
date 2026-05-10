import apiClient from './client'

export interface WorkspaceEntry {
  name: string
  kind: 'file' | 'dir'
  size: number | null
}

export interface WorkspaceTreeResponse {
  path: string
  entries: WorkspaceEntry[]
}

export interface WorkspaceContentResponse {
  path: string
  content: string
  size: number
}

export const workspaceFilesApi = {
  tree: async (
    projectId: string,
    path: string = '',
  ): Promise<WorkspaceTreeResponse> => {
    const { data } = await apiClient.get<WorkspaceTreeResponse>(
      '/api/v1/workspace-files',
      { params: { project_id: projectId, path: path || undefined } },
    )
    return data
  },
  content: async (
    projectId: string,
    path: string,
  ): Promise<WorkspaceContentResponse> => {
    const { data } = await apiClient.get<WorkspaceContentResponse>(
      '/api/v1/workspace-files/content',
      { params: { project_id: projectId, path } },
    )
    return data
  },
}
