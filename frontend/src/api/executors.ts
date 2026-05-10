import apiClient from './client'

export type ExecutorKind = 'bsgateway' | 'llm_api'

export interface ExecutorConfigResponse {
  kind: ExecutorKind
  base_url: string | null
  model: string | null
  has_api_key: boolean
  extra_config: Record<string, unknown>
}

export interface ExecutorConfigUpdate {
  kind: ExecutorKind
  base_url?: string | null
  model?: string | null
  api_key?: string | null
  extra_config?: Record<string, unknown>
}

export const executorConfigApi = {
  get: async (): Promise<ExecutorConfigResponse | null> => {
    const { data } = await apiClient.get<ExecutorConfigResponse | null>(
      '/api/v1/executor-config',
    )
    return data
  },
  upsert: async (
    body: ExecutorConfigUpdate,
  ): Promise<ExecutorConfigResponse> => {
    const { data } = await apiClient.put<ExecutorConfigResponse>(
      '/api/v1/executor-config',
      body,
    )
    return data
  },
}
