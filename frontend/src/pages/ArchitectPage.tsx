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

  // Find the active session object for name display
  const activeSession = sessions.find((s) => s.id === sessionId) || null

  // Session loaded = connected
  useEffect(() => {
    setConnected(!!sessionId)
  }, [sessionId, setConnected])

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Cleanup abort controller on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  // Load sessions list on mount
  useEffect(() => {
    architectApi.listSessions().then((list) => {
      setSessions(list)
    }).catch(() => {
      // Failed to load sessions
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Auto-open new session modal when navigated from dashboard
  useEffect(() => {
    const state = location.state as { openNewSession?: boolean } | null
    if (state?.openNewSession) {
      setNewSessionModalOpen(true)
      navigate(location.pathname, { replace: true, state: {} })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state])

  // Load session from URL param
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
        // Project-bound sessions keep chat open - no finalize panel lock
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
  const headerTitle = activeSession?.name || 'Architect'

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
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center space-y-4">
              <div className="text-text-tertiary text-lg">No session selected</div>
              <p className="text-text-muted text-sm">Select a session from the sidebar or create a new one.</p>
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
          <div className="flex items-center gap-2">
            <span className={`inline-block w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-xs text-text-secondary">{isConnected ? 'Connected' : 'Disconnected'}</span>
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
        <div className="flex-1 flex flex-col">
          {/* Messages area */}
          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            {messages.length === 0 ? (
              <div className="flex-1 flex items-center justify-center h-full">
                <div className="text-center space-y-2">
                  <p className="text-lg font-medium text-text-secondary">안녕하세요! BSNexus Architect입니다.</p>
                  <p className="text-sm text-text-muted">어떤 프로젝트를 만들고 싶으신가요?</p>
                </div>
              </div>
            ) : (
              messages.map((msg) => (
                <ChatMessage key={msg.id} message={msg} />
              ))
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input area - hidden when finalize panel is showing */}
          {!showFinalizePanel && (
            <div className="p-4 border-t border-border">
              <ChatInput
                onSend={handleSend}
                disabled={isStreaming || !isConnected}
              />
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
