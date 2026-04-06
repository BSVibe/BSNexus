export interface ExecutorConfig {
  id: string
  tenant_id: string
  name: string
  executor_type: string
  config: Record<string, unknown>
  description: string | null
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface ExecutorConfigCreate {
  name: string
  executor_type: string
  config?: Record<string, unknown>
  description?: string
  is_default?: boolean
}

export interface ExecutorConfigUpdate {
  name?: string
  config?: Record<string, unknown>
  description?: string
  is_default?: boolean
}
