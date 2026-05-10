import apiClient from './client'
import type { DirectionAckResponse, DirectionSource } from '../types/founder'

export interface DirectionCreatePayload {
  body: string
  source: DirectionSource
  project_id?: string | null
  target_hint?: string | null
}

/**
 * Direction endpoint client (greenfield G1, decision-locks A3 flat shape).
 *
 * Direction creation is the top-level founder primitive: short founder
 * input from any interface (web / mobile_web / slack / cli / voice) is
 * posted here, the backend opens a Request when a project can be
 * identified, otherwise it returns a routing prompt for the founder to
 * pick a project before BSNexus opens a request.
 */
export const directionsApi = {
  create: async (payload: DirectionCreatePayload): Promise<DirectionAckResponse> => {
    const { data } = await apiClient.post<DirectionAckResponse>(
      '/api/v1/directions',
      payload,
    )
    return data
  },
}
