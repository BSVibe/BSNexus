import apiClient from './client'
import type {
  CompositionSnapshot,
  Decision,
  Deliverable,
  ExecutionRun,
  Request as FounderRequest,
} from '../types/founder'

export const requestsApi = {
  listForProject: async (projectId: string): Promise<FounderRequest[]> => {
    const { data } = await apiClient.get<FounderRequest[]>(
      `/api/v1/projects/${projectId}/requests`,
    )
    return data
  },
  listRuns: async (requestId: string): Promise<ExecutionRun[]> => {
    const { data } = await apiClient.get<ExecutionRun[]>(
      `/api/v1/requests/${requestId}/runs`,
    )
    return data
  },
}

export const deliverablesApi = {
  listForProject: async (projectId: string): Promise<Deliverable[]> => {
    const { data } = await apiClient.get<Deliverable[]>(
      `/api/v1/projects/${projectId}/deliverables`,
    )
    return data
  },
}

export const decisionsApi = {
  listForProject: async (projectId: string): Promise<Decision[]> => {
    const { data } = await apiClient.get<Decision[]>(
      `/api/v1/projects/${projectId}/decisions`,
    )
    return data
  },
  resolve: async (
    decisionId: string,
    payload: { resolution: string; resolved_by?: string | null },
  ): Promise<Decision> => {
    const { data } = await apiClient.post<Decision>(
      `/api/v1/decisions/${decisionId}/resolve`,
      payload,
    )
    return data
  },
}

export const compositionSnapshotsApi = {
  get: async (snapshotId: string): Promise<CompositionSnapshot> => {
    const { data } = await apiClient.get<CompositionSnapshot>(
      `/api/v1/composition-snapshots/${snapshotId}`,
    )
    return data
  },
}
