import apiClient from './client'

// Taxonomy collapsed 2026-05-04 (Phase 2a) — distinguished by *infra
// dependency*, not capability. Both honor MCP / Decisions / artifact
// UX equally; the only difference is whether dispatching needs
// BSVibe's BSGateway pool.
//   bsgateway   — BSVibe infra path (BSGateway worker pool)
//   generic_llm — BSVibe-optional path (direct litellm + MCP tool loop)
// Legacy values (claude_code / codex / opencode / worker) are
// auto-lifted by the alembic migration; they don't appear on new
// responses but the API still accepts them as input for back-compat.
export type ExecutorType = 'bsgateway' | 'generic_llm'

export interface ExecutorConfig {
  id: string
  tenant_id: string
  name: string
  executor_type: ExecutorType
  config: Record<string, unknown>
  description: string | null
  is_selected: boolean
  has_api_key: boolean
  created_at: string
  updated_at: string
}

export interface ExecutorConfigCreate {
  name: string
  executor_type: ExecutorType
  config?: Record<string, unknown>
  description?: string | null
  is_selected?: boolean
  /** Plaintext API key — encrypted server-side, never round-tripped on responses. */
  api_key?: string
}

export interface ExecutorConfigUpdate {
  name?: string
  config?: Record<string, unknown>
  description?: string | null
  is_selected?: boolean
  api_key?: string
}

export const executorConfigsApi = {
  list: async (): Promise<ExecutorConfig[]> => {
    const { data } = await apiClient.get<ExecutorConfig[]>('/api/v1/executor-configs')
    return data
  },
  get: async (id: string): Promise<ExecutorConfig> => {
    const { data } = await apiClient.get<ExecutorConfig>(`/api/v1/executor-configs/${id}`)
    return data
  },
  create: async (body: ExecutorConfigCreate): Promise<ExecutorConfig> => {
    const { data } = await apiClient.post<ExecutorConfig>(
      '/api/v1/executor-configs',
      body,
    )
    return data
  },
  update: async (id: string, body: ExecutorConfigUpdate): Promise<ExecutorConfig> => {
    const { data } = await apiClient.patch<ExecutorConfig>(
      `/api/v1/executor-configs/${id}`,
      body,
    )
    return data
  },
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/api/v1/executor-configs/${id}`)
  },
}
