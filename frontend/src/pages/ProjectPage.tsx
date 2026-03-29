import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useBoard } from '../hooks/useBoard'
import { useBoardStore } from '../stores/boardStore'
import { useArchitectStore } from '../stores/architectStore'
import { useToastStore } from '../stores/toastStore'
import type { ChatMessage as ChatMessageType } from '../stores/architectStore'
import { projectsApi } from '../api/projects'
import { architectApi } from '../api/architect'
import KanbanBoard from '../components/board/KanbanBoard'
import BoardStats from '../components/board/BoardStats'
import TaskDetail from '../components/board/TaskDetail'
import PMControl from '../components/board/PMControl'
import RedesignView from '../components/board/RedesignView'
import ChatMessage from '../components/architect/ChatMessage'
import ChatInput from '../components/architect/ChatInput'
import Header from '../components/layout/Header'
import type { Task } from '../types/task'
import { MessageSquare, PanelRightClose, PanelRightOpen, Bot } from 'lucide-react'

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()

  if (!projectId) {
    return (
      <>
        <Header title="Project" />
        <div className="p-8">
          <div className="rounded-lg border border-dashed border-border p-12 text-center">
            <p className="text-text-secondary mb-4">Select a project from the Dashboard.</p>
            <button type="button" onClick={() => navigate('/')} className="text-sm text-accent hover:underline">
              Go to Dashboard
            </button>
          </div>
        </div>
      </>
    )
  }

  return <ProjectContent projectId={projectId} />
}

function ProjectContent({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const addToast = useToastStore((s) => s.addToast)
  const [chatOpen, setChatOpen] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Board state
  const { isLoading: boardLoading } = useBoard(projectId)
  const { columns, redesignTasks, selectedTask, setSelectedTask, isConnected } = useBoardStore()

  // Architect state
  const {
    sessionId,
    messages,
    isStreaming,
    setSessionId,
    setProjectId,
    setMessages,
    addMessage,
    appendToLastMessage,
    setStreaming,
    setConnected,
    clearMessages,
  } = useArchitectStore()

  // Project data
  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: !!projectId,
  })

  // Load architect session for this project
  useEffect(() => {
    setProjectId(projectId)
    architectApi.getSessionByProject(projectId)
      .then((session) => {
        setSessionId(session.id)
        setConnected(true)
        const chatMessages: ChatMessageType[] = session.messages.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          createdAt: m.created_at,
        }))
        setMessages(chatMessages)
      })
      .catch(() => {
        // No session found - that's ok
        setSessionId(null)
        setConnected(false)
        clearMessages()
      })

    return () => {
      setProjectId(null)
      setSessionId(null)
      clearMessages()
    }
  }, [projectId, setProjectId, setSessionId, setConnected, setMessages, clearMessages])

  // Auto-scroll chat
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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
      onDone: () => {
        setStreaming(false)
        // Refresh board after architect actions
        queryClient.invalidateQueries({ queryKey: ['board', projectId] })
      },
      onFinalizeReady: () => {
        // In project-bound mode, finalize_ready shouldn't appear
        setStreaming(false)
      },
      onError: (message) => {
        setStreaming(false)
        addToast(`Architect error: ${message}`, 'error')
      },
    })

    abortRef.current = controller
  }, [addMessage, appendToLastMessage, setStreaming, queryClient, projectId, addToast])

  const isRedesigning = redesignTasks.length > 0

  const handleRedesignDone = () => {
    queryClient.invalidateQueries({ queryKey: ['board', projectId] })
  }

  if (boardLoading) {
    return (
      <>
        <Header title="Project" />
        <div className="flex items-center justify-center h-64">
          <div className="text-text-secondary">Loading project...</div>
        </div>
      </>
    )
  }

  return (
    <>
      <Header
        title={project?.name || 'Project'}
        action={
          <div className="flex items-center gap-2">
            <span
              className="inline-block w-2 h-2 rounded-full"
              style={{ backgroundColor: isConnected ? 'var(--status-done)' : 'var(--status-redesign)' }}
            />
            <span className="text-xs text-text-tertiary">
              {isConnected ? 'Live' : 'Disconnected'}
            </span>
            <button
              type="button"
              onClick={() => setChatOpen(!chatOpen)}
              className="ml-2 p-1.5 rounded-md hover:bg-bg-hover text-text-secondary"
              title={chatOpen ? 'Close chat' : 'Open Architect chat'}
            >
              {chatOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
            </button>
          </div>
        }
      />

      <div className="flex h-[calc(100vh-64px)] overflow-hidden">
        {/* Main content: Board */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="px-6 pt-4 pb-3 flex items-center gap-4">
            <BoardStats />
            <PMControl projectId={projectId} />
          </div>

          {isRedesigning && (
            <div className="px-6 pb-3">
              <RedesignView
                tasks={redesignTasks as Task[]}
                onDone={handleRedesignDone}
              />
            </div>
          )}

          <div className="flex-1 overflow-auto px-6 pb-6">
            <KanbanBoard
              columns={columns}
              onTaskClick={(task: Task) => setSelectedTask(task)}
            />
          </div>
        </div>

        {/* Right panel: Architect Chat */}
        {chatOpen && (
          <div className="w-[420px] border-l border-border/40 bg-bg-surface flex flex-col shrink-0">
            <div className="px-4 py-3 border-b border-border/30 flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-accent/10 flex items-center justify-center">
                <Bot size={14} className="text-accent" />
              </div>
              <h3 className="text-sm font-semibold text-text-primary">Architect</h3>
              {sessionId && (
                <span className="ml-auto text-[11px] text-text-muted bg-bg-elevated px-2 py-0.5 rounded-md border border-border/30">
                  project-bound
                </span>
              )}
            </div>

            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
              {!sessionId ? (
                <div className="flex flex-col items-center justify-center h-full gap-3">
                  <div className="w-12 h-12 rounded-xl bg-bg-elevated flex items-center justify-center">
                    <MessageSquare size={20} className="text-text-muted" />
                  </div>
                  <p className="text-sm text-text-muted">No architect session for this project.</p>
                </div>
              ) : messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full gap-3">
                  <div className="w-12 h-12 rounded-xl bg-accent/10 flex items-center justify-center">
                    <Bot size={20} className="text-accent" />
                  </div>
                  <p className="text-sm text-text-tertiary">Start a conversation with the Architect.</p>
                </div>
              ) : (
                messages.map((msg) => (
                  <ChatMessage key={msg.id} message={msg} />
                ))
              )}
              <div ref={messagesEndRef} />
            </div>

            {sessionId && (
              <div className="p-4 border-t border-border/30">
                <ChatInput onSend={handleSend} disabled={isStreaming || !sessionId} />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Task detail modal */}
      {selectedTask && (
        <TaskDetail task={selectedTask as Task} onClose={() => setSelectedTask(null)} />
      )}
    </>
  )
}
