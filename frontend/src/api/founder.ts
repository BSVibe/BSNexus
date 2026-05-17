import apiClient from './client'
import type {
  CompositionSnapshot,
  Decision,
  DecisionResolve,
  Deliverable,
  ExecutionRun,
  Request as FounderRequest,
  RunActivity,
  RunSummaryAggregate,
  RunSummaryItem,
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
  listActivities: async (
    runId: string,
    params?: { level?: 'milestone' | 'tool' },
  ): Promise<RunActivity[]> => {
    const qs = new URLSearchParams()
    if (params?.level) qs.set('level', params.level)
    const url = qs.toString()
      ? `/api/v1/runs/${runId}/activities?${qs}`
      : `/api/v1/runs/${runId}/activities`
    const { data } = await apiClient.get<RunActivity[]>(url)
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
  /**
   * Manually re-enqueue a deliverable's verification (decision-locks A1).
   * Returns the deliverable with its current proof state — verification
   * runs asynchronously and the SSE channel pushes the eventual transition.
   */
  verify: async (deliverableId: string): Promise<Deliverable> => {
    const { data } = await apiClient.post<Deliverable>(
      `/api/v1/deliverables/${deliverableId}/verify`,
    )
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
    payload: DecisionResolve,
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

/**
 * Run-summary surface (PR7 — failure-mode dashboard backend).
 * Two query modes from one endpoint per A3 flat-REST shape: list of
 * recent runs OR aggregate counts per dominant_reply_quality.
 */
export const runSummariesApi = {
  list: async (params?: {
    projectId?: string
    days?: number
    limit?: number
  }): Promise<RunSummaryItem[]> => {
    const qs = new URLSearchParams()
    if (params?.projectId) qs.set('project_id', params.projectId)
    if (params?.days !== undefined) qs.set('days', String(params.days))
    if (params?.limit !== undefined) qs.set('limit', String(params.limit))
    const url = qs.toString() ? `/api/v1/run-summaries?${qs}` : '/api/v1/run-summaries'
    const { data } = await apiClient.get<RunSummaryItem[]>(url)
    return data
  },
  aggregate: async (params?: {
    projectId?: string
    days?: number
  }): Promise<RunSummaryAggregate> => {
    const qs = new URLSearchParams({ aggregate: 'true' })
    if (params?.projectId) qs.set('project_id', params.projectId)
    if (params?.days !== undefined) qs.set('days', String(params.days))
    const { data } = await apiClient.get<RunSummaryAggregate>(
      `/api/v1/run-summaries?${qs}`,
    )
    return data
  },
}
