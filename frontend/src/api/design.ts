import apiClient from './client'

export interface DesignSystem {
  project_id: string
  name: string
  tokens: Record<string, unknown>
  components: Record<string, unknown>
  patterns: Record<string, unknown>
  brand_voice: string | null
  path: string
}

export interface ScreenSummary {
  project_id: string
  slug: string
  path: string
  name: string
  route: string | null
}

export interface ScreenDetail {
  project_id: string
  slug: string
  path: string
  name: string
  route: string | null
  intent: string | null
  spec: Record<string, unknown>
  generated_code: string | null
}

export interface ScreenPayload {
  name: string
  route?: string | null
  intent?: string | null
  spec?: Record<string, unknown>
  generated_code?: string | null
}

export const designApi = {
  getSystem: (projectId: string) =>
    apiClient.get<DesignSystem>(`/api/v1/projects/${projectId}/design/system`).then((r) => r.data),

  upsertSystem: (projectId: string, body: Partial<DesignSystem>) =>
    apiClient
      .put<DesignSystem>(`/api/v1/projects/${projectId}/design/system`, body)
      .then((r) => r.data),

  listScreens: (projectId: string) =>
    apiClient
      .get<ScreenSummary[]>(`/api/v1/projects/${projectId}/design/screens`)
      .then((r) => r.data),

  getScreen: (projectId: string, slug: string) =>
    apiClient
      .get<ScreenDetail>(`/api/v1/projects/${projectId}/design/screens/${slug}`)
      .then((r) => r.data),

  createScreen: (projectId: string, body: ScreenPayload) =>
    apiClient
      .post<ScreenDetail>(`/api/v1/projects/${projectId}/design/screens`, body)
      .then((r) => r.data),

  updateScreen: (projectId: string, slug: string, body: ScreenPayload) =>
    apiClient
      .put<ScreenDetail>(`/api/v1/projects/${projectId}/design/screens/${slug}`, body)
      .then((r) => r.data),

  deleteScreen: (projectId: string, slug: string) =>
    apiClient.delete(`/api/v1/projects/${projectId}/design/screens/${slug}`),
}
