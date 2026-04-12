import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { agentChatApi } from '../../api/agentChat'
import { agentsApi } from '../../api/agents'
import { useChatEvents } from '../../hooks/useChatEvents'
import { useToastStore } from '../../stores/toastStore'
import ApprovalSettings from '../plan/ApprovalSettings'
import StopAllButton from '../plan/StopAllButton'
import ChatMessage from './ChatMessage'
import MentionAutocomplete from './MentionAutocomplete'

// Safety net: if the backend never publishes a response within this
// window we force-clear the typing indicator and surface a toast. The
// backend already publishes ``[Error]`` messages on failure (which
// clear the indicator on their own), but a Redis hiccup or a worker
// silently dropping the result must not leave the UI stuck forever.
const PENDING_TIMEOUT_MS = 90_000

const MIN_WIDTH = 240
const MAX_WIDTH = 600
const DEFAULT_WIDTH = 320

interface Props {
  projectId: string
}

export default function UnifiedChatSidebar({ projectId }: Props) {
  const queryClient = useQueryClient()
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  const [input, setInput] = useState('')
  const [mentionQuery, setMentionQuery] = useState<string | null>(null)
  const [mentionIndex, setMentionIndex] = useState(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isDragging = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(DEFAULT_WIDTH)

  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list(),
    // Refetch frequently so blue-dot (thinking) state from Redis is
    // picked up even after a page refresh when there is no client-side
    // pendingMessage to derive typing indicators from.
    refetchInterval: 5000,
  })

  // SSE: real-time chat events from server
  useChatEvents(projectId)

  const { data: historyData } = useQuery({
    queryKey: ['project-chat', projectId],
    queryFn: () => agentChatApi.history(projectId),
  })

  const messages = useMemo(() => historyData?.messages ?? [], [historyData])

  // Optimistic state: user message + dispatched agent names for typing indicators
  const [pendingMessage, setPendingMessage] = useState<string | null>(null)
  const [pendingAgents, setPendingAgents] = useState<string[]>([])

  // Hide optimistic user bubble once the real one arrives via SSE
  const showPendingUser = useMemo(() => {
    if (!pendingMessage) return false
    return !messages.some((m) => m.role === 'user' && m.content === pendingMessage)
  }, [messages, pendingMessage])

  // Per-agent typing: show while no response from that agent exists after the user message
  const activeTypingAgents = useMemo(() => {
    if (!pendingMessage || pendingAgents.length === 0) return []
    let lastUserIdx = -1
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user' && messages[i].content === pendingMessage) {
        lastUserIdx = i
        break
      }
    }
    if (lastUserIdx < 0) return pendingAgents // user message hasn't arrived yet
    const respondedNames = new Set<string | null>()
    for (let i = lastUserIdx + 1; i < messages.length; i++) {
      if (messages[i].role === 'assistant') respondedNames.add(messages[i].agent_name)
    }
    return pendingAgents.filter((name) => !respondedNames.has(name))
  }, [messages, pendingMessage, pendingAgents])

  const filteredAgents = useMemo(() => {
    if (mentionQuery === null) return []
    return agents.filter((a) => a.name.toLowerCase().startsWith(mentionQuery.toLowerCase()))
  }, [agents, mentionQuery])

  const addToast = useToastStore((s) => s.addToast)

  const sendMutation = useMutation({
    mutationFn: (message: string) => agentChatApi.send(projectId, message),
    onSuccess: (data) => {
      setPendingAgents(data.dispatched_agents)
    },
    onError: (err) => {
      setPendingMessage(null)
      setPendingAgents([])
      addToast(`Failed to send: ${(err as Error).message}`, 'error')
    },
  })

  // Auto-clear pending state once all agents have responded. Using
  // useEffect instead of a render-time microtask so React does not
  // clear pendingMessage/pendingAgents during the same render pass
  // that computed them (which caused the typing indicator to flash
  // away before the user saw it, and the optimistic user bubble to
  // vanish before the real SSE message arrived).
  const allDone = pendingAgents.length > 0 && activeTypingAgents.length === 0 && !showPendingUser
  useEffect(() => {
    if (allDone && pendingMessage) {
      setPendingMessage(null)
      setPendingAgents([])
    }
  }, [allDone, pendingMessage])

  // Safety net: if the backend never publishes a response within
  // PENDING_TIMEOUT_MS we force-clear and toast the user. Without this,
  // a dropped Redis publish or a silently-failed worker turn would leave
  // the typing indicator on forever.
  useEffect(() => {
    if (!pendingMessage || pendingAgents.length === 0) return
    const timer = setTimeout(() => {
      addToast(
        'No response from the agent within 90s — check the worker / LLM status.',
        'error',
      )
      setPendingMessage(null)
      setPendingAgents([])
    }, PENDING_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [pendingMessage, pendingAgents, addToast])

  const clearMutation = useMutation({
    mutationFn: () => agentChatApi.clear(projectId),
    onSuccess: () => queryClient.setQueryData(['project-chat', projectId], { messages: [] }),
  })

  // @mention detection
  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value
    setInput(val)
    const pos = e.target.selectionStart
    const textBeforeCursor = val.slice(0, pos)
    const match = textBeforeCursor.match(/(?:^|\s)@(\w*)$/)
    if (match) {
      setMentionQuery(match[1])
      setMentionIndex(0)
    } else {
      setMentionQuery(null)
    }
  }, [])

  const handleMentionSelect = useCallback((agent: { name: string }) => {
    const pos = textareaRef.current?.selectionStart ?? input.length
    const textBefore = input.slice(0, pos)
    const atIndex = textBefore.lastIndexOf('@')
    if (atIndex >= 0) {
      const before = input.slice(0, atIndex)
      const after = input.slice(pos)
      setInput(`${before}@${agent.name} ${after}`)
    }
    setMentionQuery(null)
    textareaRef.current?.focus()
  }, [input])

  const handleSend = useCallback(() => {
    const trimmed = input.trim()
    if (!trimmed || sendMutation.isPending) return
    setPendingMessage(trimmed)
    // Set temporary typing agents from @mentions in the message (will be overwritten by server response)
    const mentionMatches = [...trimmed.matchAll(/@(\S+)/g)]
    const mentionedNames = mentionMatches
      .map((m) => agents.find((a) => a.name.toLowerCase() === m[1].toLowerCase())?.name)
      .filter(Boolean) as string[]
    setPendingAgents(mentionedNames.length > 0 ? mentionedNames : ['...'])
    setInput('')
    setMentionQuery(null)
    sendMutation.mutate(trimmed)
  }, [input, sendMutation, agents])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionQuery !== null && filteredAgents.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setMentionIndex((i) => Math.min(i + 1, filteredAgents.length - 1))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setMentionIndex((i) => Math.max(i - 1, 0))
        return
      }
      if (e.key === 'Enter') {
        e.preventDefault()
        handleMentionSelect(filteredAgents[mentionIndex])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setMentionQuery(null)
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }, [mentionQuery, filteredAgents, mentionIndex, handleMentionSelect, handleSend])

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, activeTypingAgents.length])

  // Resize
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true
    startX.current = e.clientX
    startWidth.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [width])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const delta = startX.current - e.clientX
      setWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta)))
    }
    const handleMouseUp = () => {
      if (!isDragging.current) return
      isDragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  return (
    <aside
      className="h-full bg-stitch-surface-low border-l border-stitch-outline-variant/10 flex shrink-0 overflow-hidden"
      style={{ width }}
    >
      {/* Resize handle */}
      <div
        onMouseDown={handleMouseDown}
        className="w-1 cursor-col-resize hover:bg-stitch-primary/30 active:bg-stitch-primary/50 transition-colors shrink-0"
      />

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Header */}
        <div className="px-4 py-3 border-b border-stitch-outline-variant/10 flex items-center justify-between">
          <h3 className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary">Chat</h3>
          {messages.length > 0 && (
            <button
              onClick={() => clearMutation.mutate()}
              className="text-[10px] text-text-tertiary hover:text-stitch-error transition-colors"
              title="Clear chat"
            >
              clear
            </button>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
          {messages.length === 0 && !pendingMessage && (
            <div className="flex flex-col items-center justify-center h-full text-text-tertiary">
              <span className="material-symbols-outlined text-3xl mb-2 opacity-40">chat</span>
              <p className="text-xs text-center">@mention an agent to start a conversation</p>
            </div>
          )}

          {messages.map((msg) => (
            <ChatMessage key={msg.id} message={msg} />
          ))}

          {/* Optimistic user bubble */}
          {showPendingUser && pendingMessage && (
            <ChatMessage
              message={{
                id: '__pending_user',
                role: 'user',
                content: pendingMessage,
                agent_id: null,
                agent_name: null,
                created_at: new Date().toISOString(),
                actions: [],
              }}
            />
          )}

          {/* Typing indicators — combine:
              1. Client-side: agents we just dispatched (activeTypingAgents)
              2. Server-side: agents with dot=blue from the agents query
                 (survives page refresh because it's Redis-backed)
              Dedup by name so an agent doesn't show two typing bubbles. */}
          {(() => {
            const shown = new Set<string>()
            const typingBubbles: Array<{ name: string; activity?: string }> = []

            // Client-side pending (immediate, before server catches up)
            for (const name of activeTypingAgents) {
              if (name === '...') {
                typingBubbles.push({ name: '...' })
                shown.add('...')
              } else if (!shown.has(name)) {
                shown.add(name)
                typingBubbles.push({ name })
              }
            }

            // Server-side busy agents (survives refresh)
            for (const a of agents) {
              if (a.activity && !shown.has(a.name)) {
                shown.add(a.name)
                typingBubbles.push({ name: a.name, activity: a.activity })
              }
            }

            return typingBubbles.map(({ name, activity }) => (
              <ChatMessage
                key={`typing-${name}`}
                message={{
                  id: `__typing_${name}`,
                  role: 'assistant',
                  content: activity || '​',
                  agent_id: null,
                  agent_name: name === '...' ? null : name,
                  created_at: new Date().toISOString(),
                  actions: [],
                }}
                typing
              />
            ))
          })()}

          <div ref={messagesEndRef} />
        </div>

        {/* Error */}
        {sendMutation.isError && (
          <p className="text-xs text-stitch-error px-3 pb-1">
            {(sendMutation.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || (sendMutation.error as Error).message || 'Failed to send'}
          </p>
        )}

        {/* Controls — approval mode + stop all */}
        <div className="border-t border-stitch-outline-variant/10 px-3 py-2 flex items-center justify-between gap-2">
          <ApprovalSettings projectId={projectId} />
          <StopAllButton projectId={projectId} />
        </div>

        {/* Input area */}
        <div className="border-t border-stitch-outline-variant/10 p-3 relative">
          {mentionQuery !== null && filteredAgents.length > 0 && (
            <MentionAutocomplete
              agents={filteredAgents}
              query={mentionQuery}
              selectedIndex={mentionIndex}
              onSelect={handleMentionSelect}
              onDismiss={() => setMentionQuery(null)}
            />
          )}

          <div className="flex items-end gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder="@mention an agent..."
              rows={1}
              className="flex-1 px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/20 rounded-lg text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary resize-none"
              style={{ maxHeight: '120px' }}
              disabled={sendMutation.isPending}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || sendMutation.isPending}
              className="p-2 rounded-lg bg-stitch-primary text-white disabled:opacity-40 hover:bg-stitch-primary/80 transition-colors shrink-0"
            >
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>send</span>
            </button>
          </div>
        </div>
      </div>
    </aside>
  )
}
