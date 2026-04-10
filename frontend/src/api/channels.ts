import apiClient from './client'

export type ChannelKind = 'slack' | 'discord' | 'teams'

export interface ProjectChannel {
  id: string
  project_id: string
  kind: ChannelKind | string
  external_channel_id: string
  display_name: string | null
  is_active: boolean
}

export interface ChannelCreate {
  kind: string
  external_channel_id: string
  display_name?: string | null
  credentials?: Record<string, unknown>
}

export interface ChannelUpdate {
  display_name?: string | null
  credentials?: Record<string, unknown>
  is_active?: boolean
}

export const channelsApi = {
  list: (projectId: string) =>
    apiClient
      .get<ProjectChannel[]>(`/api/v1/projects/${projectId}/channels`)
      .then((r) => r.data),

  create: (projectId: string, body: ChannelCreate) =>
    apiClient
      .post<ProjectChannel>(`/api/v1/projects/${projectId}/channels`, body)
      .then((r) => r.data),

  update: (projectId: string, channelId: string, body: ChannelUpdate) =>
    apiClient
      .patch<ProjectChannel>(`/api/v1/projects/${projectId}/channels/${channelId}`, body)
      .then((r) => r.data),

  remove: (projectId: string, channelId: string) =>
    apiClient.delete(`/api/v1/projects/${projectId}/channels/${channelId}`),
}
