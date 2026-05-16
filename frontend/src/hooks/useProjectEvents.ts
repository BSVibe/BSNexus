import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { API_BASE_URL } from '../api/client'
import { getAccessToken, getActiveTenantId } from './useAuth'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting'

/**
 * Subscribe to ``GET /api/v1/events?project_id={id}`` and feed react-query
 * caches in real time. Replaces the prior 3-second ``refetchInterval``
 * loops on chat / deliverables / decisions.
 *
 * Auth: the EventSource API can't send custom headers, so the JWT goes
 * in the ``token`` query string. The backend's ``get_current_user``
 * dependency reads it the same way as the Authorization header.
 *
 * Reconnection: ``EventSource`` reconnects automatically on
 * ``onerror``; the backend ``retry:`` directive sets the gap. We track
 * the resulting state for an indicator badge in the chat header.
 */
export function useProjectEvents(projectId: string | null): ConnectionStatus {
  const queryClient = useQueryClient()
  // Live connection status driven by EventSource events. ``idle`` is
  // derived from ``projectId`` at return time so we don't need to
  // setLiveStatus('idle') inside the effect — that would trip React 19's
  // ``react-hooks/set-state-in-effect`` lint.
  const [liveStatus, setLiveStatus] = useState<ConnectionStatus>('connecting')
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!projectId) {
      return
    }

    let cancelled = false
    let es: EventSource | null = null

    async function connect() {
      const token = await getAccessToken()
      if (cancelled || !token) return
      // Tier 3.2: EventSource cannot send the X-Active-Tenant header — pass
      // the active tenant as a query param, mirroring the ?token= fallback.
      const activeTenant = await getActiveTenantId()
      if (cancelled) return
      const tenantParam = activeTenant
        ? `&active_tenant=${encodeURIComponent(activeTenant)}`
        : ''
      const url = `${API_BASE_URL}/api/v1/events?project_id=${projectId}&token=${encodeURIComponent(token)}${tenantParam}`
      setLiveStatus('connecting')
      es = new EventSource(url)
      esRef.current = es

      es.addEventListener('ready', () => setLiveStatus('open'))
      es.addEventListener('heartbeat', () => {
        // no-op; presence of the event keeps the connection alive
      })

      es.addEventListener('run_transition', () => {
        // Brief surfaces ``running``/``blocked`` runs via /api/v1/runs;
        // refetch on every transition.
        queryClient.invalidateQueries({ queryKey: ['runs', projectId] })
        queryClient.invalidateQueries({ queryKey: ['brief', projectId] })
      })

      es.addEventListener('deliverable', () => {
        queryClient.invalidateQueries({ queryKey: ['deliverables', projectId] })
        queryClient.invalidateQueries({ queryKey: ['brief', projectId] })
      })

      es.addEventListener('deliverable_proof', () => {
        // Verifier Worker stamped a new proof_state on a deliverable in
        // this project (decision-locks A1). Refetch both the deliverables
        // list and the Brief so badge + section state both update.
        queryClient.invalidateQueries({ queryKey: ['deliverables', projectId] })
        queryClient.invalidateQueries({ queryKey: ['brief', projectId] })
        queryClient.invalidateQueries({ queryKey: ['decisions', 'inbox', 'blocking'] })
      })

      es.addEventListener('decision', () => {
        queryClient.invalidateQueries({ queryKey: ['decisions', projectId] })
      })

      es.addEventListener('decision_resolved', () => {
        // Resolve API already calls publish_decision_resolved; the
        // Decisions tab must dismiss the row immediately and the
        // Inside panel flips its blocked banner.
        queryClient.invalidateQueries({ queryKey: ['decisions', projectId] })
        queryClient.invalidateQueries({ queryKey: ['runs', projectId] })
      })

      es.onerror = () => {
        // EventSource auto-reconnects on its own using the server's
        // ``retry:`` directive; we just surface the state.
        setLiveStatus('reconnecting')
      }
    }

    connect()

    return () => {
      cancelled = true
      esRef.current?.close()
      esRef.current = null
    }
  }, [projectId, queryClient])

  return projectId ? liveStatus : 'idle'
}
