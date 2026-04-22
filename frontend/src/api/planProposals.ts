import apiClient from './client'

export interface PlanProposal {
  id: string
  project_id: string
  proposer_agent_name: string | null
  proposal_type: 'phase' | 'task'
  payload: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
}

export interface ApprovalSettings {
  phase_creation: 'auto_approve' | 'require_approval'
  task_creation: 'auto_approve' | 'require_approval'
}

export const planProposalsApi = {
  list: (projectId: string, status = 'pending') =>
    apiClient
      .get<PlanProposal[]>(`/api/v1/projects/${projectId}/proposals`, { params: { status } })
      .then((r) => r.data),

  approve: (projectId: string, proposalId: string) =>
    apiClient
      .post<{ status: string; created_id: string }>(`/api/v1/projects/${projectId}/proposals/${proposalId}/approve`)
      .then((r) => r.data),

  reject: (projectId: string, proposalId: string) =>
    apiClient
      .post<{ status: string }>(`/api/v1/projects/${projectId}/proposals/${proposalId}/reject`)
      .then((r) => r.data),

  getSettings: (projectId: string) =>
    apiClient
      .get<ApprovalSettings>(`/api/v1/projects/${projectId}/proposals/settings`)
      .then((r) => r.data),

  updateSettings: (projectId: string, settings: Partial<ApprovalSettings>) =>
    apiClient
      .put<ApprovalSettings>(`/api/v1/projects/${projectId}/proposals/settings`, settings)
      .then((r) => r.data),
}
