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
      `/api/v1/requests?project_id=${projectId}`,
    )
    return data
  },
  list: async (params?: { limit?: number }): Promise<FounderRequest[]> => {
    const qs = new URLSearchParams()
    if (params?.limit !== undefined) qs.set('limit', String(params.limit))
    const url = qs.toString() ? `/api/v1/requests?${qs}` : '/api/v1/requests'
    const { data } = await apiClient.get<FounderRequest[]>(url)
    return data
  },
  listRuns: async (requestId: string): Promise<ExecutionRun[]> => {
    const { data } = await apiClient.get<ExecutionRun[]>(
      `/api/v1/runs?request_id=${requestId}`,
    )
    return data
  },
}

export const deliverablesApi = {
  listForProject: async (projectId: string): Promise<Deliverable[]> => {
    const { data } = await apiClient.get<Deliverable[]>(
      `/api/v1/deliverables?project_id=${projectId}`,
    )
    return data
  },
  list: async (params?: { limit?: number }): Promise<Deliverable[]> => {
    const qs = new URLSearchParams()
    if (params?.limit !== undefined) qs.set('limit', String(params.limit))
    const url = qs.toString() ? `/api/v1/deliverables?${qs}` : '/api/v1/deliverables'
    const { data } = await apiClient.get<Deliverable[]>(url)
    return data
  },
}

export const decisionsApi = {
  listForProject: async (projectId: string): Promise<Decision[]> => {
    const { data } = await apiClient.get<Decision[]>(
      `/api/v1/decisions?project_id=${projectId}`,
    )
    return data
  },
  list: async (params?: {
    blockingOnly?: boolean
    resolved?: boolean | null
    limit?: number
  }): Promise<Decision[]> => {
    const qs = new URLSearchParams()
    if (params?.blockingOnly) qs.set('blocking_only', 'true')
    if (params?.resolved !== undefined && params?.resolved !== null) {
      qs.set('resolved', String(params.resolved))
    }
    if (params?.limit !== undefined) qs.set('limit', String(params.limit))
    const url = qs.toString() ? `/api/v1/decisions?${qs}` : '/api/v1/decisions'
    const { data } = await apiClient.get<Decision[]>(url)
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
