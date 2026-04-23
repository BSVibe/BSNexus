import apiClient from './client'
import type { IntegrationProvider } from '../types/founder'

export interface IntegrationConfigResponse {
  provider: IntegrationProvider
  enabled: boolean
  base_url: string | null
  has_api_key: boolean
  extra_config: Record<string, unknown>
}

export interface IntegrationConfigList {
  bsage: IntegrationConfigResponse
  bsgateway: IntegrationConfigResponse
  bsupervisor: IntegrationConfigResponse
}

export interface IntegrationConfigUpdate {
  enabled?: boolean
  base_url?: string | null
  api_key?: string | null
  extra_config?: Record<string, unknown>
}

export interface IntegrationTestResult {
  ok: boolean
  status: 'healthy' | 'unreachable' | 'unauthorized' | 'disabled'
  detail: string | null
}

export const integrationsApi = {
  list: async (): Promise<IntegrationConfigList> => {
    const { data } = await apiClient.get<IntegrationConfigList>(
      '/api/v1/integrations',
    )
    return data
  },
  update: async (
    provider: IntegrationProvider,
    body: IntegrationConfigUpdate,
  ): Promise<IntegrationConfigResponse> => {
    const { data } = await apiClient.patch<IntegrationConfigResponse>(
      `/api/v1/integrations/${provider}`,
      body,
    )
    return data
  },
  test: async (provider: IntegrationProvider): Promise<IntegrationTestResult> => {
    const { data } = await apiClient.post<IntegrationTestResult>(
      `/api/v1/integrations/${provider}/test`,
    )
    return data
  },
}
