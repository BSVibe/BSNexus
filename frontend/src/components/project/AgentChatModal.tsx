import { useState, useRef, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Modal } from '../common'
import { agentChatApi, type ChatMessageOut } from '../../api/agentChat'
import type { Agent } from '../../types/agent'

interface Props {
  open: boolean
  onClose: () => void
  agent: Agent
  projectId: string
}

export default function AgentChatModal({ open, onClose, agent, projectId }: Props) {
  const queryClient = useQueryClient()
  const [input, setInput] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const { data: historyData } = useQuery({
    queryKey: ['agent-chat', projectId, agent.id],
    queryFn: () => agentChatApi.history(projectId, agent.id),
    enabled: open,
  })

  const messages = historyData?.messages ?? []

  const sendMutation = useMutation({
    mutationFn: (message: string) => agentChatApi.send(projectId, agent.id, message),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['agent-chat', projectId, agent.id] })
      // If tasks were created, invalidate board
      if (data.message.actions.length > 0) {
        queryClient.invalidateQueries({ queryKey: ['board', projectId] })
      }
    },
  })

  const handleSend = useCallback(() => {
    const trimmed = input.trim()
    if (!trimmed || sendMutation.isPending) return
    setInput('')
    sendMutation.mutate(trimmed)
  }, [input, sendMutation])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend],
  )

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, sendMutation.isPending])

  // Auto-focus textarea
  useEffect(() => {
    if (open) textareaRef.current?.focus()
  }, [open])

  return (
    <Modal open={open} onClose={onClose} title={`Chat with ${agent.name}`} width={640}>
      <div className="flex flex-col" style={{ height: 'min(60vh, 500px)' }}>
        {/* Messages */}
        <div className="flex-1 overflow-y-auto space-y-3 mb-3 pr-1">
          {messages.length === 0 && !sendMutation.isPending && (
            <div className="flex flex-col items-center justify-center h-full text-text-tertiary">
              <span className="material-symbols-outlined text-3xl mb-2 opacity-40">chat</span>
              <p className="text-xs">Start a conversation with {agent.name}</p>
              <p className="text-[10px] mt-1 opacity-60">Ask to create tasks, discuss architecture, get status updates</p>
            </div>
          )}

          {messages.map((msg) => (
            <ChatBubble key={msg.id} message={msg} />
          ))}

          {sendMutation.isPending && (
            <div className="flex items-start gap-2">
              <div className="w-6 h-6 rounded-full bg-stitch-primary/20 flex items-center justify-center shrink-0">
                <span className="material-symbols-outlined text-stitch-primary" style={{ fontSize: '14px' }}>
                  smart_toy
                </span>
              </div>
              <div className="bg-stitch-surface-low border border-stitch-outline-variant/10 rounded-lg px-3 py-2">
                <div className="flex items-center gap-1.5">
                  <div className="w-1.5 h-1.5 bg-stitch-primary rounded-full animate-bounce" />
                  <div className="w-1.5 h-1.5 bg-stitch-primary rounded-full animate-bounce" style={{ animationDelay: '0.15s' }} />
                  <div className="w-1.5 h-1.5 bg-stitch-primary rounded-full animate-bounce" style={{ animationDelay: '0.3s' }} />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Error */}
        {sendMutation.isError && (
          <p className="text-xs text-stitch-error mb-2 px-1">
            {(sendMutation.error as Error).message || 'Failed to send message'}
          </p>
        )}

        {/* Input */}
        <div className="flex items-end gap-2 border-t border-stitch-outline-variant/10 pt-3">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Message ${agent.name}...`}
            rows={1}
            className="flex-1 px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-lg text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary resize-none"
            style={{ maxHeight: '120px' }}
            disabled={sendMutation.isPending}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || sendMutation.isPending}
            className="p-2 rounded-lg bg-stitch-primary text-white disabled:opacity-40 hover:bg-stitch-primary/80 transition-colors shrink-0"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>send</span>
          </button>
        </div>
      </div>
    </Modal>
  )
}

function ChatBubble({ message }: { message: ChatMessageOut }) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex items-start gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div
        className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 ${
          isUser ? 'bg-stitch-surface-high' : 'bg-stitch-primary/20'
        }`}
      >
        <span
          className={`material-symbols-outlined ${isUser ? 'text-text-secondary' : 'text-stitch-primary'}`}
          style={{ fontSize: '14px' }}
        >
          {isUser ? 'person' : 'smart_toy'}
        </span>
      </div>

      <div
        className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
          isUser
            ? 'bg-stitch-primary/15 text-text-primary'
            : 'bg-stitch-surface-low border border-stitch-outline-variant/10 text-text-primary'
        }`}
      >
        <div className="whitespace-pre-wrap break-words">{message.content}</div>

        {/* Task creation notifications */}
        {message.actions && message.actions.length > 0 && (
          <div className="mt-2 pt-2 border-t border-stitch-outline-variant/10 space-y-1">
            {message.actions.map((action, i) => (
              <div key={i} className="flex items-center gap-1.5 text-[10px] text-stitch-primary">
                <span className="material-symbols-outlined" style={{ fontSize: '12px' }}>
                  {action.type === 'task_created' ? 'add_task' : 'edit'}
                </span>
                <span>
                  {action.type === 'task_created' ? 'Created task:' : 'Modified task:'} {action.title}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
