import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useBoard } from '../hooks/useBoard'
import { useBoardStore } from '../stores/boardStore'
import { projectsApi } from '../api/projects'
import KanbanBoard from '../components/board/KanbanBoard'
import BoardStats from '../components/board/BoardStats'
import TaskDetail from '../components/board/TaskDetail'
import FileBrowser from '../components/workspace/FileBrowser'
import AgentSidebar from '../components/project/AgentSidebar'
import AgentChatModal from '../components/project/AgentChatModal'
import AddTaskModal from '../components/project/AddTaskModal'
import TimelineView from '../components/project/TimelineView'
import DesignView from '../components/project/DesignView'
import Header from '../components/layout/Header'
import type { Task } from '../types/task'
import type { Agent } from '../types/agent'

type TabId = 'board' | 'files' | 'timeline' | 'design'

const TABS: { id: TabId; label: string; icon: string }[] = [
  { id: 'board', label: 'Board', icon: 'view_kanban' },
  { id: 'files', label: 'Files', icon: 'folder' },
  { id: 'timeline', label: 'Timeline', icon: 'timeline' },
  { id: 'design', label: 'Design', icon: 'palette' },
]

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
            <button type="button" onClick={() => navigate('/dashboard')} className="text-sm text-stitch-primary hover:underline">
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
  const [activeTab, setActiveTab] = useState<TabId>('board')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [addTaskOpen, setAddTaskOpen] = useState(false)
  const [chatAgent, setChatAgent] = useState<Agent | null>(null)

  // Board state
  const { isLoading: boardLoading } = useBoard(projectId)
  const { columns, selectedTask, setSelectedTask, isConnected } = useBoardStore()

  // Project data
  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: !!projectId,
  })

  const handleChatWithAgent = (agent: Agent) => {
    setChatAgent(agent)
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
      {/* Header with tabs */}
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
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="p-2 rounded-md hover:bg-stitch-surface-container text-text-secondary transition-colors"
              title={sidebarOpen ? 'Hide agents' : 'Show agents'}
            >
              <span className="material-symbols-outlined">{sidebarOpen ? 'right_panel_close' : 'right_panel_open'}</span>
            </button>
          </div>
        }
      />

      {/* Tabs in sub-header */}
      <div className="px-8 flex items-center gap-1 border-b border-stitch-outline-variant/10 bg-stitch-surface">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-bold uppercase tracking-widest transition-colors border-b-2 -mb-px ${
              activeTab === tab.id
                ? 'text-stitch-primary border-stitch-primary'
                : 'text-text-tertiary border-transparent hover:text-text-secondary'
            }`}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Main content area */}
      <div className="flex h-[calc(100vh-112px)] overflow-hidden">
        {/* Center content */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {activeTab === 'board' && (
            <>
              <div className="px-8 pt-4 pb-3">
                <BoardStats projectName={project?.name} />
              </div>
              <div className="flex-1 overflow-auto px-8 pb-6">
                <KanbanBoard
                  columns={columns}
                  onTaskClick={(task: Task) => setSelectedTask(task)}
                  onAddTask={() => setAddTaskOpen(true)}
                />
              </div>
            </>
          )}

          {activeTab === 'files' && (
            <div className="flex-1 overflow-hidden">
              <FileBrowser projectId={projectId} />
            </div>
          )}

          {activeTab === 'timeline' && (
            <TimelineView projectId={projectId} />
          )}

          {activeTab === 'design' && (
            <DesignView projectId={projectId} />
          )}
        </div>

        {/* Right sidebar: Agents */}
        {sidebarOpen && (
          <AgentSidebar
            projectId={projectId}
            onChatWithAgent={handleChatWithAgent}
          />
        )}
      </div>

      {/* Task detail modal */}
      {selectedTask && (
        <TaskDetail task={selectedTask as Task} onClose={() => setSelectedTask(null)} />
      )}

      {/* Add task modal */}
      {project && (
        <AddTaskModal
          open={addTaskOpen}
          onClose={() => setAddTaskOpen(false)}
          project={project}
        />
      )}

      {/* Agent chat modal */}
      {chatAgent && (
        <AgentChatModal
          open={!!chatAgent}
          onClose={() => setChatAgent(null)}
          agent={chatAgent}
          projectId={projectId}
        />
      )}
    </>
  )
}
