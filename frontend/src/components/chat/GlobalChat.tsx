import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import { statusTone } from '../../lib/tone'
import { StatusDot } from '../common/Badge'
import { conversationApi, type Message, type SendMessageResponse } from '../../api/conversation'
import type { Project } from '../../api/projects'

interface GlobalChatProps {
  projects: Project[]
  currentProject: Project | null
  collapsed: boolean
  onToggleCollapsed: () => void
}

interface DisplayMessage extends Message {
  routed_to: string[]
  inferred?: boolean
  /**
   * A synthetic marker placed by ``send`` so we can show the routing chip
   * before the server round-trip lands. Server responses replace this.
   */
  optimistic?: boolean
}

export default function GlobalChat({
  projects,
  currentProject,
  collapsed,
  onToggleCollapsed,
}: GlobalChatProps) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [draft, setDraft] = useState('')
  const [mentions, setMentions] = useState<Project[]>([])
  // The mention menu's open state is derived from the @-query. A
  // manual dismiss (escape / selection) sets ``menuDismissed`` so we
  // don't reopen until the query changes again. menuIdx is keyed to
  // atQuery so we reset on query change without a setState-in-effect.
  const [menuDismissed, setMenuDismissed] = useState(false)
  const [menuIdx, setMenuIdx] = useState(0)
  const [scopeToCurrent, setScopeToCurrent] = useState(false)
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const projectQueries = useQueries({
    queries: projects.map((p) => ({
      queryKey: ['messages', p.id],
      queryFn: () => conversationApi.list(p.id),
      // Orchestrator writes assistant replies asynchronously — keep the
      // chat rail in sync without a manual refresh.
      refetchInterval: 3000,
    })),
  })

  const messages: DisplayMessage[] = useMemo(() => {
    const all: DisplayMessage[] = []
    projectQueries.forEach((q, i) => {
      const pid = projects[i]?.id
      if (!pid || !q.data) return
      q.data.forEach((m) =>
        all.push({
          ...m,
          routed_to: [pid],
        }),
      )
    })
    all.sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    )
    return all
  }, [projectQueries, projects])

  const visible = useMemo(() => {
    if (!scopeToCurrent || !currentProject) return messages
    return messages.filter((m) => m.routed_to.includes(currentProject.id))
  }, [messages, scopeToCurrent, currentProject])

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [visible.length, collapsed])

  const atQuery = useMemo(() => {
    const m = draft.match(/@([\w\- ]*)$/)
    return m ? m[1] : null
  }, [draft])

  const mentionCandidates = useMemo(() => {
    if (atQuery === null) return []
    const q = atQuery.toLowerCase()
    return projects
      .filter((p) => !mentions.find((x) => x.id === p.id))
      .filter((p) => p.name.toLowerCase().includes(q))
      .slice(0, 6)
  }, [atQuery, projects, mentions])

  const menuOpen =
    !menuDismissed && atQuery !== null && mentionCandidates.length > 0

  // Reset highlighted index when the query text changes. Keying the
  // reset off atQuery (not the candidates array) avoids thrashing when
  // the candidate list reshuffles mid-typing.
  const lastAtQueryRef = useRef<string | null>(atQuery)
  if (lastAtQueryRef.current !== atQuery) {
    lastAtQueryRef.current = atQuery
    if (menuIdx !== 0) setMenuIdx(0)
    if (menuDismissed) setMenuDismissed(false)
  }

  useEffect(() => {
    if (taRef.current) {
      taRef.current.style.height = 'auto'
      taRef.current.style.height = `${Math.min(140, taRef.current.scrollHeight)}px`
    }
  }, [draft])

  const addMention = (p: Project) => {
    setMentions((m) => [...m, p])
    setDraft((d) => d.replace(/@[\w\- ]*$/, ''))
    setMenuDismissed(true)
    setTimeout(() => taRef.current?.focus(), 10)
  }

  const removeMention = (pid: string) =>
    setMentions((m) => m.filter((x) => x.id !== pid))

  const resolveRouting = (text: string): { projectId: string | null; inferred: boolean } => {
    if (mentions.length) return { projectId: mentions[0].id, inferred: false }
    const hits = projects.filter((p) =>
      text.toLowerCase().includes(p.name.split(' ')[0].toLowerCase()),
    )
    if (hits.length) return { projectId: hits[0].id, inferred: true }
    if (currentProject) return { projectId: currentProject.id, inferred: true }
    return { projectId: null, inferred: true }
  }

  const sendMutation = useMutation({
    mutationFn: async (payload: { content: string; projectId: string }): Promise<SendMessageResponse> => {
      return conversationApi.send(payload.projectId, payload.content)
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['messages', vars.projectId] })
      queryClient.invalidateQueries({ queryKey: ['requests', vars.projectId] })
    },
  })

  const [unrouted, setUnrouted] = useState<
    | { content: string; at: string }
    | null
  >(null)

  const send = () => {
    const text = draft.trim()
    if (!text) return
    const { projectId } = resolveRouting(text)
    if (!projectId) {
      setUnrouted({ content: text, at: new Date().toISOString() })
      return
    }
    sendMutation.mutate({ content: text, projectId })
    setDraft('')
    setMentions([])
    setUnrouted(null)
  }

  const onKey: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (menuOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setMenuIdx((i) => Math.min(mentionCandidates.length - 1, i + 1))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setMenuIdx((i) => Math.max(0, i - 1))
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        const pick = mentionCandidates[menuIdx]
        if (pick) addMention(pick)
        return
      }
      if (e.key === 'Escape') {
        setMenuDismissed(true)
        return
      }
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      send()
    }
    if (e.key === 'Backspace' && draft === '' && mentions.length) {
      setMentions((m) => m.slice(0, -1))
    }
  }

  if (collapsed) {
    return (
      <aside className="cr">
        <div className="chat-collapsed-rail">
          <button
            type="button"
            className="btn btn-icon"
            title="Expand chat (⌘/)"
            onClick={onToggleCollapsed}
          >
            <I.ChevLeft size={16} />
          </button>
          <div
            style={{
              width: 24,
              height: 1,
              background: 'var(--border-subtle)',
            }}
          />
          <button
            type="button"
            className="btn btn-icon"
            title="Chat"
            onClick={onToggleCollapsed}
          >
            <I.Chat size={16} />
          </button>
          <div
            style={{
              writingMode: 'vertical-rl',
              fontSize: 11,
              color: 'var(--text-tertiary)',
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              marginTop: 12,
            }}
          >
            Talk to the company
          </div>
          <div style={{ flex: 1 }} />
          <span className="mono faded" style={{ fontSize: 10 }}>
            {messages.length}
          </span>
        </div>
      </aside>
    )
  }

  return (
    <aside className="cr">
      <div
        className="chat-hd"
        style={{ justifyContent: 'flex-end', padding: '8px 12px', minHeight: 40 }}
      >
        <button
          type="button"
          className="btn btn-icon"
          title="Collapse (⌘/)"
          onClick={onToggleCollapsed}
        >
          <I.ChevRight size={14} />
        </button>
      </div>

      {currentProject && (
        <div className="chat-route-pick">
          <span>Scope</span>
          <button
            type="button"
            className={`seg ${!scopeToCurrent ? 'on' : ''}`}
            onClick={() => setScopeToCurrent(false)}
          >
            All projects
          </button>
          <button
            type="button"
            className={`seg ${scopeToCurrent ? 'on' : ''}`}
            onClick={() => setScopeToCurrent(true)}
          >
            {currentProject.name}
          </button>
        </div>
      )}

      <div className="chat-scroll" ref={scrollRef}>
        {visible.length === 0 && !unrouted && (
          <div
            style={{
              textAlign: 'center',
              padding: '32px 8px',
              color: 'var(--text-tertiary)',
              fontSize: 12,
            }}
          >
            Nothing here in this scope yet.
          </div>
        )}
        {visible.map((m) => (
          <ChatBubble
            key={m.id}
            m={m}
            projects={projects}
            onOpenProject={(pid) => navigate(`/projects/${pid}`)}
            onInspectRequest={(rid) =>
              document.dispatchEvent(
                new CustomEvent('bsn:open-inspector', { detail: { requestId: rid } }),
              )
            }
          />
        ))}
        {unrouted && (
          <UnroutedNotice
            content={unrouted.content}
            onRetry={(pid) => {
              sendMutation.mutate({ content: unrouted.content, projectId: pid })
              setDraft('')
              setMentions([])
              setUnrouted(null)
            }}
            projects={projects}
          />
        )}
        {sendMutation.isPending && (
          <div
            style={{
              display: 'flex',
              gap: 8,
              alignItems: 'center',
              color: 'var(--text-tertiary)',
              fontSize: 12,
              padding: '4px 2px',
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                background: 'var(--accent)',
                borderRadius: 99,
                animation: 'pulse 1.2s infinite',
              }}
            />
            routing · composing…
          </div>
        )}
      </div>

      <div className="chat-composer">
        <div className="chat-box">
          {mentions.length > 0 && (
            <div className="chat-ctx-chips">
              {mentions.map((m) => (
                <span key={m.id} className="mention-chip">
                  @ {m.name}
                  <span className="x" onClick={() => removeMention(m.id)}>
                    ×
                  </span>
                </span>
              ))}
            </div>
          )}
          {menuOpen && (
            <div className="mention-menu">
              {mentionCandidates.map((p, i) => (
                <div
                  key={p.id}
                  className={`mention-menu-item ${i === menuIdx ? 'active' : ''}`}
                  onMouseEnter={() => setMenuIdx(i)}
                  onClick={() => addMention(p)}
                >
                  <StatusDot tone={statusTone(p.status)} size={6} />
                  <span style={{ flex: 1 }}>{p.name}</span>
                  <span className="mono faded" style={{ fontSize: 10 }}>
                    {truncId(p.id)}
                  </span>
                </div>
              ))}
            </div>
          )}
          <textarea
            ref={taRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            rows={1}
            placeholder={
              mentions.length
                ? 'Add direction…'
                : 'Say something. @mention a project or just talk.'
            }
            style={{
              width: '100%',
              resize: 'none',
              background: 'transparent',
              border: 'none',
              outline: 'none',
              color: 'var(--gray-100)',
              fontSize: 13,
              lineHeight: '20px',
              minHeight: 20,
            }}
          />
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              paddingTop: 6,
            }}
          >
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              title="Mention a project"
              onClick={() => {
                setDraft((d) => `${d}@`)
                taRef.current?.focus()
              }}
            >
              @
            </button>
            {!mentions.length && currentProject && (
              <span className="faded" style={{ fontSize: 10 }}>
                hint: <kbd>@</kbd> to target a project
              </span>
            )}
            <span style={{ flex: 1 }} />
            <span className="faded" style={{ fontSize: 10 }}>
              <kbd>⌘</kbd>
              <kbd>↵</kbd>
            </span>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={!draft.trim() || sendMutation.isPending}
              onClick={send}
            >
              <I.Send size={12} />
            </button>
          </div>
        </div>
      </div>
    </aside>
  )
}

function ChatBubble({
  m,
  projects,
  onOpenProject,
  onInspectRequest,
}: {
  m: DisplayMessage
  projects: Project[]
  onOpenProject: (id: string) => void
  onInspectRequest: (id: string) => void
}) {
  const isUser = m.role === 'user'
  return (
    <div
      className="fade-in"
      style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
    >
      <div className={isUser ? 'chat-bubble-u' : 'chat-bubble-ai'}>
        {isUser ? (
          m.content
        ) : (
          <div className="md">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
          </div>
        )}
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          flexWrap: 'wrap',
          paddingLeft: 2,
        }}
      >
        <span className="faded" style={{ fontSize: 10 }}>
          {relTime(m.created_at)}
        </span>
        {m.routed_to.length === 0 && !isUser && (
          <span className="route-chip" style={{ fontSize: 10 }}>
            workspace · no project
          </span>
        )}
        {m.routed_to.map((pid) => {
          const p = projects.find((x) => x.id === pid)
          if (!p) return null
          return (
            <button
              type="button"
              key={pid}
              className="route-chip"
              style={{ fontSize: 10, cursor: 'pointer' }}
              onClick={() => onOpenProject(p.id)}
              title={`Open ${p.name}`}
            >
              <span style={{ color: 'var(--text-tertiary)' }}>→</span>
              <StatusDot tone={statusTone(p.status)} size={6} />
              {p.name}
            </button>
          )
        })}
        {m.request_id && (
          <button
            type="button"
            className="mono faded"
            style={{
              fontSize: 10,
              cursor: 'pointer',
              background: 'none',
              border: 'none',
              padding: 0,
            }}
            onClick={() => m.request_id && onInspectRequest(m.request_id)}
            title="Inspect this request"
          >
            · {truncId(m.request_id)}
          </button>
        )}
      </div>
    </div>
  )
}

function UnroutedNotice({
  content,
  onRetry,
  projects,
}: {
  content: string
  onRetry: (projectId: string) => void
  projects: Project[]
}) {
  return (
    <div
      style={{
        padding: '10px 12px',
        border: '1px dashed var(--border-default)',
        borderRadius: 'var(--r-md)',
        background: 'var(--bg-elevated)',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
    >
      <div
        style={{
          fontSize: 12,
          color: 'var(--gray-200)',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <I.Alert size={12} /> No project matched — pick one to route:
      </div>
      <div style={{ fontSize: 12, color: 'var(--gray-100)' }}>{content}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {projects.map((p) => (
          <button
            key={p.id}
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onRetry(p.id)}
          >
            <StatusDot tone={statusTone(p.status)} size={6} /> {p.name}
          </button>
        ))}
      </div>
    </div>
  )
}
