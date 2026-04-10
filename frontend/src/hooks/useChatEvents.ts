import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { ChatHistoryResponse, ChatMessageOut } from '../api/agentChat'

const MAX_RETRIES = 5

/**
 * Subscribe to a project's chat SSE stream.
 *
 * Handles three event types:
 *   - `message_created`: append to chat history cache
 *   - `history_cleared`: empty the cache
 *   - `agent_status`: invalidate agents cache so UI reflects busy/online
 */
export function useChatEvents(projectId: string | undefined) {
  const queryClient = useQueryClient()
  const sourceRef = useRef<EventSource | null>(null)
  const retriesRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!projectId) return

    const queryKey = ['project-chat', projectId]

    const appendMessage = (msg: ChatMessageOut) => {
      queryClient.setQueryData<ChatHistoryResponse>(queryKey, (prev) => {
        const messages = prev?.messages ?? []
        if (messages.some((m) => m.id === msg.id)) return prev
        return { messages: [...messages, msg] }
      })
    }

    const clearMessages = () => {
      queryClient.setQueryData<ChatHistoryResponse>(queryKey, { messages: [] })
    }

    const handleMessageCreated = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as ChatMessageOut
        appendMessage(data)
        const actions = data.actions || []
        if (actions.some((a) => a.type === 'task_created')) {
          queryClient.invalidateQueries({ queryKey: ['board', projectId] })
        }
        if (actions.some((a) => a.type.startsWith('goal_'))) {
          queryClient.invalidateQueries({ queryKey: ['goals', projectId] })
        }
      } catch { /* ignore parse errors */ }
    }

    const handleHistoryCleared = () => {
      clearMessages()
    }

    const handleAgentStatus = () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] })
      // Plan view's agent status bar reads a different query — invalidate
      // it too so the dot flips to ``thinking`` the moment chat dispatches
      // an agent.
      queryClient.invalidateQueries({ queryKey: ['agent-status', projectId] })
    }

    const connect = () => {
      if (sourceRef.current) {
        sourceRef.current.close()
        sourceRef.current = null
      }

      const source = new EventSource(`/api/v1/projects/${projectId}/chat/events`)

      source.addEventListener('message_created', handleMessageCreated as EventListener)
      source.addEventListener('history_cleared', handleHistoryCleared as EventListener)
      source.addEventListener('agent_status', handleAgentStatus as EventListener)

      source.onopen = () => {
        retriesRef.current = 0
      }

      source.onerror = () => {
        source.close()
        sourceRef.current = null
        if (retriesRef.current < MAX_RETRIES) {
          const delay = Math.min(1000 * Math.pow(2, retriesRef.current), 30000)
          retriesRef.current += 1
          reconnectTimerRef.current = setTimeout(connect, delay)
        }
      }

      sourceRef.current = source
    }

    connect()

    return () => {
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
