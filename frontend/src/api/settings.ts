import apiClient from './client'

export interface GlobalSettings {
  llm_api_key: string | null
  llm_model: string | null
  llm_base_url: string | null
  default_executor_type: string
}

export const settingsApi = {
  get: () => apiClient.get<GlobalSettings>('/api/v1/settings').then(r => r.data),
  update: (settings: Partial<GlobalSettings>) =>
    apiClient.put<GlobalSettings>('/api/v1/settings', settings).then(r => r.data),
}
