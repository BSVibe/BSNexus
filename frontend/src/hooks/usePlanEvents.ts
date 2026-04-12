import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { PlanTreeResponse, TaskStatus } from '../api/planTree'
import { getAccessToken } from './useAuth'

const MAX_RETRIES = 10

interface TaskTransitionPayload {
  task_id: string
  from_status: TaskStatus
  to_status: TaskStatus
  actor: string
}

/**
 * Subscribe to a project's plan SSE stream.
 *
 * Same lifecycle pattern as useChatEvents — see that hook for the
 * rationale on cancelledRef, visibilitychange recovery, and retry reset.
 */
export function usePlanEvents(projectId: string | undefined) {
  const queryClient = useQueryClient()
  const sourceRef = useRef<EventSource | null>(null)
  const retriesRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    if (!projectId) return

    cancelledRef.current = false
    const treeKey = ['plan-tree', projectId]
    const patchTaskStatus = (payload: TaskTransitionPayload) => {
      queryClient.setQueryData<PlanTreeResponse>(treeKey, (prev) => {
        if (!prev) return prev
        return {
          ...prev,
          phases: prev.phases.map((phase) => ({
            ...phase,
            tasks: phase.tasks.map((task) =>
              task.id === payload.task_id ? { ...task, status: payload.to_status } : task
            ),
          })),
        }
      })
      queryClient.invalidateQueries({ queryKey: ['agents'] })
    }

    const handleTaskTransition = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as TaskTransitionPayload
        patchTaskStatus(data)
      } catch {
        /* ignore parse errors */
      }
    }

    const handlePhaseAdvanced = () => {
      queryClient.invalidateQueries({ queryKey: treeKey })
    }


    const close = () => {
      if (sourceRef.current) {
        sourceRef.current.close()
        sourceRef.current = null
      }
    }

    const connect = async () => {
      if (cancelledRef.current) return
      close()

      const token = await getAccessToken()
      if (cancelledRef.current) return

      const url = token
        ? `/api/v1/projects/${projectId}/plan-tree/events?token=${encodeURIComponent(token)}`
        : `/api/v1/projects/${projectId}/plan-tree/events`
      const source = new EventSource(url)

      source.addEventListener('task_transition', handleTaskTransition as EventListener)
      source.addEventListener('phase_advanced', handlePhaseAdvanced as EventListener)

      source.onopen = () => {
        retriesRef.current = 0
      }

      source.onerror = () => {
        source.close()
        sourceRef.current = null
        if (cancelledRef.current) return
        if (retriesRef.current < MAX_RETRIES) {
          const delay = Math.min(1000 * Math.pow(2, retriesRef.current), 30000)
          retriesRef.current += 1
          reconnectTimerRef.current = setTimeout(() => void connect(), delay)
        }
      }

      sourceRef.current = source
    }

    void connect()

    const onVisibility = () => {
      if (document.visibilityState === 'visible' && !sourceRef.current) {
        retriesRef.current = 0
        void connect()
      }
      if (document.visibilityState === 'visible') {
        queryClient.invalidateQueries({ queryKey: treeKey })
        queryClient.invalidateQueries({ queryKey: ['agents'] })
      }
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      cancelledRef.current = true
      document.removeEventListener('visibilitychange', onVisibility)
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      close()
    }
  }, [projectId, queryClient])
}
