export type DesignSessionStatus = 'active' | 'project_bound' | 'cancelled'
export type MessageRole = 'user' | 'assistant'

export interface DesignMessage {
  id: string
  session_id: string
  role: MessageRole
  content: string
  created_at: string
}

export interface DesignSession {
  id: string
  project_id: string | null
  worker_id: string | null
  name: string | null
  status: DesignSessionStatus
  created_at: string
  updated_at: string
  messages: DesignMessage[]
}

export interface LLMConfigInput {
  api_key: string
  model?: string
  base_url?: string
}

export interface CreateSessionRequest {
  name?: string
  worker_id?: string
}

export interface MessageRequest {
  content: string
}

export interface DesignMessageResponse {
  id: string
  session_id: string
  role: MessageRole
  content: string
  created_at: string
  finalize_ready?: boolean
  design_context?: string
}

export interface FinalizeRequest {
  repo_path: string
  pm_llm_config?: LLMConfigInput
}

export interface AddTaskRequest {
  phase_id: string
  request_text: string
  llm_config?: LLMConfigInput
}

export interface AddTaskResponse {
  id: string
  title: string
  description: string | null
  priority: string
  worker_prompt: Record<string, unknown> | null
  qa_prompt: Record<string, unknown> | null
}
