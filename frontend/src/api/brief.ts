import apiClient from './client'
import type { BriefResponse } from '../types/founder'

/**
 * Brief endpoint client (decision-locks A2).
 *
 * Same shape powers the project Brief surface and the Home company brief
 * — every interface (web, mobile, future Slack/email/voice) consumes
 * this single payload.
 */
export const briefApi = {
  /** Project-scoped Brief — pass the project id. */
  forProject: async (projectId: string, params?: { limit?: number }): Promise<BriefResponse> => {
    const qs = new URLSearchParams({ project_id: projectId })
    if (params?.limit !== undefined) qs.set('limit', String(params.limit))
    const { data } = await apiClient.get<BriefResponse>(`/api/v1/brief?${qs}`)
    return data
  },
  /** Tenant-wide company Brief — Home Dashboard summary. */
  company: async (params?: { limit?: number }): Promise<BriefResponse> => {
    const qs = new URLSearchParams()
    if (params?.limit !== undefined) qs.set('limit', String(params.limit))
    const url = qs.toString() ? `/api/v1/brief?${qs}` : '/api/v1/brief'
    const { data } = await apiClient.get<BriefResponse>(url)
    return data
  },
}
