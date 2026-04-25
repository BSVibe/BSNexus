import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { API_BASE_URL } from '../api/client'
import type { Message } from '../api/conversation'
import { getAccessToken } from './useAuth'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting'

/**
 * Subscribe to ``GET /api/v1/projects/{id}/events`` and feed react-query
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
  const [status, setStatus] = useState<ConnectionStatus>('idle')
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!projectId) {
      setStatus('idle')
      return
    }

    let cancelled = false
    let es: EventSource | null = null

    async function connect() {
      const token = await getAccessToken()
      if (cancelled || !token) return
      const url = `${API_BASE_URL}/api/v1/projects/${projectId}/events?token=${encodeURIComponent(token)}`
      setStatus('connecting')
      es = new EventSource(url)
      esRef.current = es

      es.addEventListener('ready', () => setStatus('open'))
      es.addEventListener('heartbeat', () => {
        // no-op; presence of the event keeps the connection alive
      })

      es.addEventListener('message', (e: MessageEvent) => {
        try {
          const data: Message & { type?: string } = JSON.parse(e.data)
          queryClient.setQueryData<Message[]>(['messages', projectId], (old) => {
            const list = old ?? []
            if (list.find((m) => m.id === data.id)) return list
            return [...list, data].sort(
              (a, b) =>
                new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
            )
          })
        } catch {
          /* ignore parse errors */
        }
      })

      es.addEventListener('run_transition', () => {
        // ExecutionRun status / Inspector view care about these.
        queryClient.invalidateQueries({ queryKey: ['runs', projectId] })
      })

      es.addEventListener('deliverable', () => {
        queryClient.invalidateQueries({ queryKey: ['deliverables', projectId] })
      })

      es.addEventListener('decision', () => {
        queryClient.invalidateQueries({ queryKey: ['decisions', projectId] })
      })

      es.onerror = () => {
        // EventSource auto-reconnects on its own using the server's
        // ``retry:`` directive; we just surface the state.
        setStatus('reconnecting')
      }
    }

    connect()

    return () => {
      cancelled = true
      esRef.current?.close()
      esRef.current = null
    }
  }, [projectId, queryClient])

  return status
}
