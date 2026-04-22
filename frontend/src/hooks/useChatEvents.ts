import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { ChatHistoryResponse, ChatMessageOut } from '../api/agentChat'
import { useAgentProcessingStore } from '../stores/agentProcessingStore'
import { getAccessToken } from './useAuth'

/**
 * Subscribe to a project's chat SSE stream.
 *
 * Handles these event types:
 *   - `text_delta`: incremental streaming content for a pre-allocated
 *     assistant message id — upserts a placeholder and appends deltas
 *     so the UI shows text appearing as the model generates it
 *   - `message_created`: authoritative row — replaces any streaming
 *     placeholder with the same id
 *   - `history_cleared`: empty the cache
 *   - `agent_status`: invalidate agents cache so UI reflects busy/online
 *
 * Key design points:
 *   - `getAccessToken()` is async, so `connect()` is async. A
 *     cancellation guard prevents orphaned EventSources when the
 *     effect cleans up while the token fetch is in flight.
 *   - Retries reset on `visibilitychange` so a backgrounded tab
 *     that kills the TCP connection can always reconnect when the
 *     user comes back.
 *   - `retriesRef` is reset on every successful `onopen`, so
 *     transient mid-session errors don't permanently exhaust the
 *     retry budget.
 */

const MAX_RETRIES = 10

export function useChatEvents(projectId: string | undefined) {
  const queryClient = useQueryClient()
  const sourceRef = useRef<EventSource | null>(null)
  const retriesRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    if (!projectId) return

    cancelledRef.current = false
    const queryKey = ['project-chat', projectId]

    // Replace an existing row by id if present (streaming placeholder);
    // otherwise append. Used by both message_created (authoritative) and
    // text_delta (placeholder append/extend).
    const upsertMessage = (msg: ChatMessageOut) => {
      queryClient.setQueryData<ChatHistoryResponse>(queryKey, (prev) => {
        const messages = prev?.messages ?? []
        const idx = messages.findIndex((m) => m.id === msg.id)
        if (idx === -1) return { messages: [...messages, msg] }
        const next = messages.slice()
        next[idx] = msg
        return { messages: next }
      })
    }

    const appendDeltaToMessage = (
      id: string,
      delta: string,
      meta: { agent_id?: string | null; agent_name?: string | null },
    ) => {
      queryClient.setQueryData<ChatHistoryResponse>(queryKey, (prev) => {
        const messages = prev?.messages ?? []
        const idx = messages.findIndex((m) => m.id === id)
        if (idx === -1) {
          // First delta — create a streaming placeholder.
          const placeholder: ChatMessageOut = {
            id,
            role: 'assistant',
            content: delta,
            agent_id: meta.agent_id ?? null,
            agent_name: meta.agent_name ?? null,
            task_id: null,
            created_at: new Date().toISOString(),
            actions: [],
            streaming: true,
          }
          return { messages: [...messages, placeholder] }
        }
        const existing = messages[idx]
        // Once the authoritative message_created row has arrived
        // (streaming=false), ignore stray late deltas.
        if (existing.streaming === false) return prev
        const next = messages.slice()
        next[idx] = { ...existing, content: existing.content + delta, streaming: true }
        return { messages: next }
      })
    }

    const clearMessages = () => {
      queryClient.setQueryData<ChatHistoryResponse>(queryKey, { messages: [] })
    }

    const handleMessageCreated = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as ChatMessageOut
        // Authoritative server row replaces any streaming placeholder.
        upsertMessage({ ...data, streaming: false })
        const actions = data.actions || []
        // Invalidate plan tree on any task/phase/goal/decision tool action
        if (actions.some((a) =>
          a.type.includes('task') || a.type.includes('phase') || a.type.includes('decision')
        )) {
          queryClient.invalidateQueries({ queryKey: ['plan-tree', projectId] })
        }
        if (actions.some((a) => a.type.includes('goal'))) {
          queryClient.invalidateQueries({ queryKey: ['goals', projectId] })
        }
        if (actions.some((a) => a.type.includes('proposal'))) {
          queryClient.invalidateQueries({ queryKey: ['proposals', projectId] })
        }
      } catch {
        /* ignore parse errors */
      }
    }

    const handleTextDelta = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as {
          text: string
          message_id?: string
          agent_id?: string
          agent_name?: string
        }
        if (!data.message_id || !data.text) return
        appendDeltaToMessage(data.message_id, data.text, {
          agent_id: data.agent_id ?? null,
          agent_name: data.agent_name ?? null,
        })
      } catch {
        /* ignore parse errors */
      }
    }

    const handleHistoryCleared = () => {
      clearMessages()
    }

    const handleTaskTransition = () => {
      // Task state changed — agent status dot is derived from task state,
      // so invalidate agents + plan tree to reflect the new state.
      queryClient.invalidateQueries({ queryKey: ['agents'] })
      queryClient.invalidateQueries({ queryKey: ['plan-tree', projectId] })
    }

    const close = () => {
      if (sourceRef.current) {
        sourceRef.current.close()
        sourceRef.current = null
      }
    }

    const connect = async () => {
      // Guard: if effect cleaned up while we were awaiting, bail.
      if (cancelledRef.current) return

      close()

      const token = await getAccessToken()
      if (cancelledRef.current) return

      const url = token
        ? `/api/v1/projects/${projectId}/chat/events?token=${encodeURIComponent(token)}`
        : `/api/v1/projects/${projectId}/chat/events`
      const source = new EventSource(url)

      const handleToolEnd = () => {
        // Tool executed — refresh plan tree immediately
        queryClient.invalidateQueries({ queryKey: ['plan-tree', projectId] })
        queryClient.invalidateQueries({ queryKey: ['goals', projectId] })
      }

      const handleAgentProcessing = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data) as {
            agent_id: string
            agent_name: string
            status: 'started' | 'completed' | 'update'
            mode: string
            activity?: string
          }
          const store = useAgentProcessingStore.getState()
          if (data.status === 'started') {
            store.setProcessing(data.agent_id, data.agent_name, data.mode, data.activity)
          } else if (data.status === 'update') {
            store.updateActivity(data.agent_id, data.activity || '')
          } else {
            store.clearProcessing(data.agent_id)
          }
        } catch {
          /* ignore parse errors */
        }
      }

      source.addEventListener('message_created', handleMessageCreated as EventListener)
      source.addEventListener('text_delta', handleTextDelta as EventListener)
      source.addEventListener('history_cleared', handleHistoryCleared as EventListener)
      source.addEventListener('task_transition', handleTaskTransition as EventListener)
      source.addEventListener('tool_end', handleToolEnd as EventListener)
      source.addEventListener('agent_processing', handleAgentProcessing as EventListener)

      source.onopen = () => {
        retriesRef.current = 0
      }

      source.onerror = () => {
        source.close()
        sourceRef.current = null
        // Intentionally do NOT clear the processing store here. A single
        // SSE hiccup would otherwise wipe every green dot and make live
        // agents look idle until the next agent_processing event — which
        // may not fire for minutes if the agent is mid-LLM-turn. The
        // backend's agent_processing Redis key (TTL 600s) plus the
        // /agents refetchInterval (30s) are the source of truth on
        // reconnect; the zustand store is an optimistic overlay.
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

    // When the tab comes back into focus, force-reconnect if the SSE
    // stream died while backgrounded. Reset retry count so exhausted
    // retries from the background period don't block recovery.
    //
    // IMPORTANT: Do NOT invalidate the chat query here. The chat cache
    // is maintained by SSE appendMessage — invalidating it triggers a
    // refetch from /chat that races with SSE and can overwrite the
    // cache with stale data, making new messages disappear. If SSE
    // was down and messages were lost, the reconnect above will pick
    // them up from the stream (cursor resumes from last seen id).
    const onVisibility = () => {
      if (document.visibilityState === 'visible' && !sourceRef.current) {
        retriesRef.current = 0
        void connect()
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
