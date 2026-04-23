import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { conversationApi, type Message, type MessageIntent } from '../../api/conversation'

interface DirectionViewProps {
  projectId?: string
}

export default function DirectionView({ projectId }: DirectionViewProps) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [lastExtraction, setLastExtraction] = useState<{
    intent: MessageIntent
    request_id: string | null
    request_created: boolean
    intent_summary: string | null
  } | null>(null)

  const { data: messages = [], isLoading } = useQuery<Message[]>({
    queryKey: ['messages', projectId],
    queryFn: () => conversationApi.list(projectId!),
    enabled: Boolean(projectId),
  })

  const sendMutation = useMutation({
    mutationFn: (content: string) => conversationApi.send(projectId!, content),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['messages', projectId] })
      queryClient.invalidateQueries({ queryKey: ['requests', projectId] })
      setDraft('')
      setLastExtraction({
        intent: result.intent,
        request_id: result.request_id,
        request_created: result.request_created,
        intent_summary: result.intent_summary,
      })
    },
  })

  const chipLabel = useMemo(() => {
    if (!lastExtraction) return null
    if (lastExtraction.intent === 'request' && lastExtraction.request_created) {
      return `요청이 열렸어요 — “${lastExtraction.intent_summary}”`
    }
    if (lastExtraction.intent === 'modification') {
      return `요청이 업데이트됐어요 — “${lastExtraction.intent_summary}”`
    }
    if (lastExtraction.intent === 'question') {
      return '질문으로 분류됨 (요청 안 생성)'
    }
    return null
  }, [lastExtraction])

  if (!projectId) {
    return (
      <div className="flex h-full items-center justify-center bg-bg-primary text-sm text-text-tertiary">
        Select a project first.
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col bg-bg-primary">
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-lg font-semibold text-text-primary">Direction</h2>
        <p className="text-sm text-text-secondary">
          Tell the company what you want. They&rsquo;ll decompose it, run it, and come back with results.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-3xl space-y-3">
          {isLoading && <p className="text-sm text-text-tertiary">Loading…</p>}
          {!isLoading && messages.length === 0 && (
            <p className="mt-16 text-center text-sm text-text-tertiary">
              Say something to kick off. The company reads it, classifies it, and turns it into a request.
            </p>
          )}
          {messages.map((msg) => (
            <MessageRow key={msg.id} message={msg} />
          ))}
        </div>
      </div>

      {chipLabel && (
        <div className="border-t border-border bg-bg-surface px-6 py-2 text-xs text-accent">
          {chipLabel}
        </div>
      )}

      <footer className="border-t border-border p-4">
        <div className="mx-auto max-w-3xl">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              const text = draft.trim()
              if (text) sendMutation.mutate(text)
            }}
            className="flex gap-2"
          >
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="What do you want the company to work on?"
              className="flex-1 rounded-lg border border-border bg-bg-input px-4 py-3 text-sm text-text-primary placeholder:text-text-tertiary focus:border-accent focus:outline-none"
              rows={3}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault()
                  const text = draft.trim()
                  if (text) sendMutation.mutate(text)
                }
              }}
            />
            <button
              type="submit"
              disabled={!draft.trim() || sendMutation.isPending}
              className="self-end rounded-lg bg-accent px-4 py-2 text-sm font-medium text-bg-primary disabled:opacity-40"
            >
              {sendMutation.isPending ? 'Sending…' : 'Send'}
            </button>
          </form>
          <p className="mt-1 text-[10px] text-text-tertiary">⌘+Enter to send</p>
        </div>
      </footer>
    </div>
  )
}

function MessageRow({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2 text-sm ${
          isUser ? 'bg-accent text-bg-primary' : 'bg-bg-card text-text-primary'
        }`}
      >
        <p className="whitespace-pre-wrap">{message.content}</p>
        {message.request_id && (
          <p className={`mt-1 text-[10px] ${isUser ? 'opacity-70' : 'text-text-tertiary'}`}>
            attached to request {message.request_id.slice(0, 8)}
          </p>
        )}
      </div>
    </div>
  )
}
