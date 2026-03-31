import { useEffect, useRef, useCallback, useState } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { useArchitectStore } from '../stores/architectStore'
import { useToastStore } from '../stores/toastStore'
import type { ChatMessage as ChatMessageType } from '../stores/architectStore'
import { architectApi } from '../api/architect'
import type { DesignSession } from '../types/architect'
import ChatMessage from '../components/architect/ChatMessage'
import ChatInput from '../components/architect/ChatInput'
import SessionList from '../components/architect/SessionList'
import NewSessionModal from '../components/architect/NewSessionModal'
import FinalizePanel from '../components/architect/FinalizePanel'
import Header from '../components/layout/Header'

function stripDesignContext(content: string): string {
  return content.replace(/<design_context>[\s\S]*?<\/design_context>/g, '').trim()
}

export default function ArchitectPage() {
  const { sessionId: paramSessionId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const {
    sessionId,
    sessions,
    messages,
    isStreaming,
    setSessionId,
    setSessions,
    setMessages,
    addMessage,
    appendToLastMessage,
    setStreaming,
    setConnected,
    clearMessages,
  } = useArchitectStore()

  const addToast = useToastStore((s) => s.addToast)
  const [newSessionModalOpen, setNewSessionModalOpen] = useState(false)
  const [showFinalizePanel, setShowFinalizePanel] = useState(false)
  const [designSummary, setDesignSummary] = useState('')
  const [finalizedProjectId, setFinalizedProjectId] = useState<string | null>(null)

  const activeSession = sessions.find((s) => s.id === sessionId) || null

  useEffect(() => {
    setConnected(!!sessionId)
  }, [sessionId, setConnected])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    architectApi.listSessions().then((list) => {
      setSessions(list)
    }).catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const state = location.state as { openNewSession?: boolean } | null
    if (state?.openNewSession) {
      setNewSessionModalOpen(true)
      navigate(location.pathname, { replace: true, state: {} })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state])

  useEffect(() => {
    if (paramSessionId && paramSessionId !== sessionId) {
      loadSession(paramSessionId)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramSessionId])

  const loadSession = async (id: string) => {
    try {
      const session: DesignSession = await architectApi.getSession(id)
      setSessionId(id)
      const chatMessages: ChatMessageType[] = session.messages.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.role === 'assistant' ? stripDesignContext(m.content) : m.content,
        createdAt: m.created_at,
      }))
      setMessages(chatMessages)

      if (session.status === 'project_bound' && session.project_id) {
        setFinalizedProjectId(session.project_id)
        setShowFinalizePanel(false)
      } else {
        setFinalizedProjectId(null)
        setShowFinalizePanel(false)
      }
    } catch {
      // Session not found
    }
  }

  const handleCreateSession = async () => {
    const session = await architectApi.createSession({})
    setSessionId(session.id)
    setNewSessionModalOpen(false)
    clearMessages()
    setSessions([session, ...sessions])
    navigate(`/architect/${session.id}`)
    loadSession(session.id)
  }

  const handleSend = useCallback((content: string) => {
    const currentSessionId = useArchitectStore.getState().sessionId
    if (!currentSessionId || useArchitectStore.getState().isStreaming) return

    addMessage({
      id: crypto.randomUUID(),
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
    })

    setStreaming(true)
    let firstChunk = true

    const controller = architectApi.streamMessage(currentSessionId, content, {
      onChunk: (text) => {
        if (firstChunk) {
          firstChunk = false
          addMessage({
            id: crypto.randomUUID(),
            role: 'assistant',
            content: text,
            isStreaming: true,
            createdAt: new Date().toISOString(),
          })
        } else {
          appendToLastMessage(text)
        }
      },
      onDone: (fullText) => {
        setStreaming(false)
        const state = useArchitectStore.getState()
        const updated = [...state.messages]
        if (updated.length > 0) {
          const last = updated[updated.length - 1]
          const finalContent = fullText && fullText.length >= last.content.length
            ? fullText : last.content
          updated[updated.length - 1] = { ...last, content: finalContent, isStreaming: false }
          setMessages(updated)
        }
      },
      onFinalizeReady: (designContext) => {
        if (designContext) {
          setDesignSummary(designContext)
        } else {
          const currentState = useArchitectStore.getState()
          const lastAssistant = [...currentState.messages].reverse().find(m => m.role === 'assistant')
          setDesignSummary(lastAssistant?.content || '')
        }
        setShowFinalizePanel(true)
      },
      onError: (message) => {
        setStreaming(false)
        addMessage({
          id: crypto.randomUUID(),
          role: 'assistant',
          content: `Error: ${message}`,
          createdAt: new Date().toISOString(),
        })
      },
    })

    abortRef.current = controller
  }, [addMessage, appendToLastMessage, setStreaming, setMessages])

  const handleFinalize = async (repoPath: string) => {
    if (!sessionId) throw new Error('No active session')
    const result = await architectApi.finalize(sessionId, {
      repo_path: repoPath,
    })
    setFinalizedProjectId(result.id)
    return result
  }

  const handleDeleteSession = async (id: string) => {
    if (!confirm('Are you sure you want to delete this session?')) return
    try {
      await architectApi.deleteSession(id)
      const updated = sessions.filter((s) => s.id !== id)
      setSessions(updated)
      if (sessionId === id) {
        setSessionId(null)
        clearMessages()
        setShowFinalizePanel(false)
        navigate('/architect')
      }
    } catch {
      addToast('Failed to delete session.')
    }
  }

  const handleBatchDeleteSessions = async (ids: string[]) => {
    if (!confirm(`Are you sure you want to delete ${ids.length} sessions?`)) return
    try {
      await architectApi.batchDeleteSessions(ids)
      const updated = sessions.filter((s) => !ids.includes(s.id))
      setSessions(updated)
      if (sessionId && ids.includes(sessionId)) {
        setSessionId(null)
        clearMessages()
        setShowFinalizePanel(false)
        navigate('/architect')
      }
    } catch {
      addToast('Failed to delete sessions.')
    }
  }

  const handleNewSession = () => {
    setNewSessionModalOpen(true)
  }

  const handleSelectSession = (id: string) => {
    navigate(`/architect/${id}`)
    loadSession(id)
  }

  const isConnected = !!sessionId
  const headerTitle = activeSession?.name || 'AI Architect Conversation'

  if (!sessionId) {
    return (
      <>
        <Header title="Architect" />
        <div className="flex h-[calc(100vh-8rem)]">
          <SessionList
            sessions={sessions}
            activeSessionId={null}
            onSelect={handleSelectSession}
            onNew={handleNewSession}
            onDelete={handleDeleteSession}
            onBatchDelete={handleBatchDeleteSessions}
          />
          <div className="flex-1 flex items-center justify-center bg-stitch-surface">
            <div className="text-center space-y-5 max-w-sm">
              <div className="relative mx-auto w-20 h-20">
                <div className="absolute inset-0 bg-stitch-primary/10 rounded-2xl" />
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className="material-symbols-outlined text-stitch-primary text-4xl" style={{ fontVariationSettings: "'FILL' 1" }}>psychology</span>
                </div>
              </div>
              <div>
                <h2 className="text-white text-lg font-semibold mb-2">BSNexus Architect</h2>
                <p className="text-text-secondary text-sm leading-relaxed">
                  Select a session from the sidebar or create a new one to start designing your project.
                </p>
              </div>
              <div className="flex items-center justify-center gap-4 text-[11px] text-text-muted">
                <span className="flex items-center gap-1.5">
                  <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>auto_awesome</span>
                  AI-powered design
                </span>
                <span className="w-1 h-1 rounded-full bg-stitch-outline-variant" />
                <span>Auto-decomposition</span>
              </div>
            </div>
          </div>
        </div>
        <NewSessionModal
          open={newSessionModalOpen}
          onClose={() => setNewSessionModalOpen(false)}
          onCreateSession={handleCreateSession}
        />
      </>
    )
  }

  return (
    <>
      <Header
        title={headerTitle}
        action={
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium tracking-wide text-stitch-primary">
                Session: {activeSession?.name || sessionId.slice(0, 8)}
              </span>
            </div>
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-stitch-surface-container border border-stitch-outline-variant/20">
              <span className={`inline-block w-2 h-2 rounded-full ${isConnected ? 'bg-stitch-primary' : 'bg-stitch-error'}`} />
              <span className="text-[11px] text-text-secondary">{isConnected ? 'Connected' : 'Disconnected'}</span>
            </div>
            <button className="p-2 text-text-secondary hover:text-white transition-all">
              <span className="material-symbols-outlined">account_tree</span>
            </button>
            <button className="p-2 text-text-secondary hover:text-white transition-all">
              <span className="material-symbols-outlined">more_vert</span>
            </button>
          </div>
        }
      />
      <div className="flex h-[calc(100vh-8rem)]">
        <SessionList
          sessions={sessions}
          activeSessionId={sessionId}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
          onDelete={handleDeleteSession}
          onBatchDelete={handleBatchDeleteSessions}
        />
        <div className="flex-1 flex flex-col bg-stitch-surface">
          {/* Messages area */}
          <div className="flex-1 overflow-y-auto">
            <div className="max-w-4xl mx-auto w-full p-8 space-y-10">
              {messages.length === 0 ? (
                <div className="flex-1 flex items-center justify-center h-full">
                  <div className="text-center space-y-4">
                    <div className="w-16 h-16 rounded-2xl bg-stitch-primary/20 flex items-center justify-center mx-auto">
                      <span className="material-symbols-outlined text-stitch-primary text-3xl" style={{ fontVariationSettings: "'FILL' 1" }}>bolt</span>
                    </div>
                    <div>
                      <p className="text-lg font-semibold text-white">BSNexus Architect</p>
                      <p className="text-sm text-text-secondary mt-1">Describe the project you'd like to build.</p>
                    </div>
                  </div>
                </div>
              ) : (
                messages.map((msg) => (
                  <ChatMessage key={msg.id} message={msg} />
                ))
              )}
              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Input area */}
          {!showFinalizePanel && (
            <div className="p-6 bg-gradient-to-t from-stitch-surface via-stitch-surface to-transparent">
              <div className="max-w-4xl mx-auto">
                <ChatInput
                  onSend={handleSend}
                  disabled={isStreaming || !isConnected}
                />
                <p className="text-center text-[10px] text-text-tertiary mt-3 opacity-50 tracking-wide uppercase">
                  AI can hallucinate. Verify critical components before deployment.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Finalize panel (right side) */}
        {showFinalizePanel && (
          <FinalizePanel
            designSummary={designSummary}
            onConfirm={handleFinalize}
            onCancel={() => setShowFinalizePanel(false)}
            onGoToBoard={(projectId) => navigate(`/projects/${projectId}`)}
            finalizedProjectId={finalizedProjectId}
          />
        )}
      </div>

      <NewSessionModal
        open={newSessionModalOpen}
        onClose={() => setNewSessionModalOpen(false)}
        onCreateSession={handleCreateSession}
      />
    </>
  )
}
