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

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()

  if (!projectId) {
    return (
      <>
        <Header title="Project" />
        <div className="p-8">
          <div className="rounded-lg border border-dashed border-stitch-outline-variant/30 p-12 text-center">
            <p className="text-text-secondary mb-4">Select a project from the Dashboard.</p>
            <button type="button" onClick={() => navigate('/')} className="text-sm text-stitch-primary hover:underline">
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
        queryClient.invalidateQueries({ queryKey: ['board', projectId] })
      },
      onFinalizeReady: () => {
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
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-stitch-primary border-t-transparent" />
        </div>
      </>
    )
  }

  return (
    <>
      <Header
        title={project?.name || 'Project'}
        action={
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-stitch-surface-container border border-stitch-outline-variant/20">
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ backgroundColor: isConnected ? 'var(--status-done)' : 'var(--status-redesign)' }}
              />
              <span className="text-[11px] font-medium text-text-secondary">{isConnected ? 'Live' : 'Offline'}</span>
            </div>
            <button
              type="button"
              onClick={() => setChatOpen(!chatOpen)}
              className="p-2 rounded-md hover:bg-stitch-surface-container text-text-secondary transition-colors"
              title={chatOpen ? 'Close chat' : 'Open Architect chat'}
            >
              <span className="material-symbols-outlined">{chatOpen ? 'right_panel_close' : 'right_panel_open'}</span>
            </button>
          </div>
        }
      />

      <div className="flex h-[calc(100vh-64px)] overflow-hidden">
        {/* Main content: Board */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="px-8 pt-6">
            {/* Project header */}
            <div className="flex items-end justify-between gap-6 mb-6">
              <div>
                <div className="flex items-center gap-3 mb-2">
                  <h2 className="text-4xl font-extrabold tracking-[-0.04em] text-white">{project?.name || 'Project'}</h2>
                  <span className="px-3 py-1 rounded-full bg-stitch-secondary-container text-stitch-on-secondary-container text-xs font-bold uppercase tracking-widest">
                    {project?.status || 'Active'}
                  </span>
                </div>
                {project?.description && (
                  <p className="text-text-secondary max-w-2xl">{project.description}</p>
                )}
              </div>
              <div className="flex items-center gap-3">
                <PMControl projectId={projectId} />
              </div>
            </div>
          </div>

          {isRedesigning && (
            <div className="px-8 pb-3">
              <RedesignView
                tasks={redesignTasks as Task[]}
                onDone={handleRedesignDone}
              />
            </div>
          )}

          <div className="px-8 pb-4">
            <BoardStats projectName={project?.name} />
          </div>

          <div className="flex-1 overflow-auto px-8 pb-6">
            <KanbanBoard
              columns={columns}
              onTaskClick={(task: Task) => setSelectedTask(task)}
            />
          </div>
        </div>

        {/* Right panel: Architect Chat drawer */}
        {chatOpen && (
          <aside className="w-80 h-full bg-stitch-surface-low border-l border-stitch-outline-variant/10 flex flex-col shrink-0">
            <div className="py-6 px-5">
              <h2 className="text-sm font-bold uppercase tracking-widest text-text-secondary mb-1">Architect Chat</h2>
              {sessionId && (
                <span className="text-[10px] text-stitch-primary font-bold">project-bound</span>
              )}
            </div>

            <div className="flex-1 overflow-y-auto px-5 space-y-4">
              {!sessionId ? (
                <div className="flex flex-col items-center justify-center h-full gap-3">
                  <div className="w-12 h-12 rounded-xl bg-stitch-surface-container flex items-center justify-center">
                    <span className="material-symbols-outlined text-text-muted">chat</span>
                  </div>
                  <p className="text-sm text-text-tertiary">No architect session for this project.</p>
                </div>
              ) : messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full gap-3">
                  <div className="w-12 h-12 rounded-xl bg-stitch-primary/10 flex items-center justify-center">
                    <span className="material-symbols-outlined text-stitch-primary" style={{ fontVariationSettings: "'FILL' 1" }}>bolt</span>
                  </div>
                  <p className="text-sm text-text-secondary">Start a conversation with the Architect.</p>
                </div>
              ) : (
                messages.map((msg) => (
                  <ChatMessage key={msg.id} message={msg} />
                ))
              )}
              <div ref={messagesEndRef} />
            </div>

            {sessionId && (
              <div className="p-4 border-t border-stitch-outline-variant/10">
                <ChatInput onSend={handleSend} disabled={isStreaming || !sessionId} />
              </div>
            )}
          </aside>
        )}
      </div>

      {/* Task detail modal */}
      {selectedTask && (
        <TaskDetail task={selectedTask as Task} onClose={() => setSelectedTask(null)} />
      )}
    </>
  )
}
