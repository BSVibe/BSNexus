import apiClient from './client'

export interface WorkspaceFileEntry {
  path: string
  size: number
}

export interface WorkspaceFileContent {
  path: string
  content: string
}

export const workspaceFilesApi = {
  list: async (projectId: string): Promise<WorkspaceFileEntry[]> => {
    const { data } = await apiClient.get<WorkspaceFileEntry[]>(
      `/api/v1/projects/${projectId}/files`,
    )
    return data
  },
  read: async (projectId: string, path: string): Promise<WorkspaceFileContent> => {
    const { data } = await apiClient.get<WorkspaceFileContent>(
      `/api/v1/projects/${projectId}/files/${encodeURI(path)}`,
    )
    return data
  },
}
