import apiClient from './client'

export type ExecutorType = 'generic_llm' | 'claude_code' | 'bsgateway' | 'codex' | 'worker'

export interface ExecutorConfig {
  id: string
  tenant_id: string
  name: string
  executor_type: ExecutorType
  config: Record<string, unknown>
  description: string | null
  is_selected: boolean
  created_at: string
  updated_at: string
}

export interface ExecutorConfigCreate {
  name: string
  executor_type: ExecutorType
  config?: Record<string, unknown>
  description?: string | null
  is_selected?: boolean
}

export interface ExecutorConfigUpdate {
  name?: string
  config?: Record<string, unknown>
  description?: string | null
  is_selected?: boolean
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
