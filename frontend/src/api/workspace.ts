import apiClient from './client'
import type { FileListResponse, FileContentResponse, GitHubConnection } from '../types/workspace'

export const workspaceApi = {
  listFiles: (projectId: string, path = '', recursive = false) =>
    apiClient.get<FileListResponse>(`/api/v1/projects/${projectId}/files`, {
      params: { path, recursive },
    }).then((r) => r.data),

  readFile: (projectId: string, path: string) =>
    apiClient.get<FileContentResponse>(`/api/v1/projects/${projectId}/files/content`, {
      params: { path },
    }).then((r) => r.data),

  // GitHub
  githubStatus: (projectId: string) =>
    apiClient.get<GitHubConnection>(`/api/v1/projects/${projectId}/github/status`).then((r) => r.data),

  githubConnect: (projectId: string, repoUrl: string, token: string, branch = 'main') =>
    apiClient.post<GitHubConnection>(`/api/v1/projects/${projectId}/github/connect`, {
      repo_url: repoUrl, token, branch,
    }).then((r) => r.data),

  githubSync: (projectId: string) =>
    apiClient.post(`/api/v1/projects/${projectId}/github/sync`).then((r) => r.data),

  githubPush: (projectId: string) =>
    apiClient.post(`/api/v1/projects/${projectId}/github/push`).then((r) => r.data),

  githubDisconnect: (projectId: string) =>
    apiClient.delete(`/api/v1/projects/${projectId}/github`).then((r) => r.data),
}
