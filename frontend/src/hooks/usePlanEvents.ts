import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { PlanTreeResponse, TaskStatus } from '../api/planTree'
import { getAccessToken } from './useAuth'

const MAX_RETRIES = 5

interface TaskTransitionPayload {
  task_id: string
  from_status: TaskStatus
  to_status: TaskStatus
  actor: string
}

/**
 * Subscribe to a project's plan SSE stream.
 *
 * Patches the plan-tree query cache directly so the tree updates without a
 * refetch. Falls back to invalidating the cache on phase advance / agent
 * status events.
 */
export function usePlanEvents(projectId: string | undefined) {
  const queryClient = useQueryClient()
  const sourceRef = useRef<EventSource | null>(null)
  const retriesRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!projectId) return

    const treeKey = ['plan-tree', projectId]
    const agentKey = ['agent-status', projectId]

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
      // Agent status dot depends on whether any task is running for the agent.
      queryClient.invalidateQueries({ queryKey: agentKey })
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

    const handleAgentStatusChanged = () => {
      queryClient.invalidateQueries({ queryKey: agentKey })
    }

    let cancelled = false
    const connect = async () => {
      if (sourceRef.current) {
        sourceRef.current.close()
        sourceRef.current = null
      }

      // EventSource cannot send custom headers; pipe the bearer token
      // through ``?token=`` instead. See useChatEvents for the same
      // pattern + rationale.
      const token = await getAccessToken()
      if (cancelled) return
      const url = token
        ? `/api/v1/projects/${projectId}/plan-tree/events?token=${encodeURIComponent(token)}`
        : `/api/v1/projects/${projectId}/plan-tree/events`
      const source = new EventSource(url)

      source.addEventListener('task_transition', handleTaskTransition as EventListener)
      source.addEventListener('phase_advanced', handlePhaseAdvanced as EventListener)
      source.addEventListener('agent_status_changed', handleAgentStatusChanged as EventListener)

      source.onopen = () => {
        retriesRef.current = 0
      }

      source.onerror = () => {
        source.close()
        sourceRef.current = null
        if (retriesRef.current < MAX_RETRIES) {
          const delay = Math.min(1000 * Math.pow(2, retriesRef.current), 30000)
          retriesRef.current += 1
          reconnectTimerRef.current = setTimeout(() => void connect(), delay)
        }
      }

      sourceRef.current = source
    }

    void connect()

    return () => {
      cancelled = true
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (sourceRef.current) {
        sourceRef.current.close()
        sourceRef.current = null
      }
    }
  }, [projectId, queryClient])
}
