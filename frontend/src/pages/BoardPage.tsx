import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useBoard } from '../hooks/useBoard'
import { useBoardStore } from '../stores/boardStore'
import { projectsApi } from '../api/projects'
import KanbanBoard from '../components/board/KanbanBoard'
import BoardStats from '../components/board/BoardStats'
import TaskDetail from '../components/board/TaskDetail'
import PMControl from '../components/board/PMControl'
import RedesignView from '../components/board/RedesignView'
import Header from '../components/layout/Header'
import type { Task } from '../types/task'

export default function BoardPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()

  if (!projectId) {
    return (
      <>
        <Header title="Board" />
        <div className="p-8">
          <div className="rounded-xl border border-dashed border-stitch-outline-variant/30 p-16 text-center">
            <p className="text-text-secondary mb-4">
              Select a project from the Dashboard to view its board.
            </p>
            <button
              type="button"
              onClick={() => navigate('/')}
              className="text-sm text-stitch-primary hover:underline transition-colors"
            >
              Go to Dashboard
            </button>
          </div>
        </div>
      </>
    )
  }

  return <BoardContent projectId={projectId} />
}

function BoardContent({ projectId }: { projectId: string }) {
  const { isLoading } = useBoard(projectId)
  const { columns, redesignTasks, selectedTask, setSelectedTask, isConnected } = useBoardStore()
  const queryClient = useQueryClient()

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: !!projectId,
  })

  const isRedesigning = redesignTasks.length > 0

  const handleRedesignDone = () => {
    queryClient.invalidateQueries({ queryKey: ['board', projectId] })
  }

  if (isLoading) {
    return (
      <>
        <Header title="Board" />
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-stitch-primary border-t-transparent" />
        </div>
      </>
    )
  }

  return (
    <>
      <Header
        title={project?.name || 'Board'}
        action={
          <div className="flex items-center gap-2.5">
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-stitch-surface-container border border-stitch-outline-variant/20">
              <span
                className="inline-block w-1.5 h-1.5 rounded-full"
                style={{ backgroundColor: isConnected ? 'var(--status-done)' : 'var(--status-redesign)' }}
              />
              <span className="text-[11px] font-medium text-text-secondary">{isConnected ? 'Live' : 'Offline'}</span>
            </div>
          </div>
        }
      />

      {isRedesigning ? (
        <RedesignView tasks={redesignTasks as Task[]} onDone={handleRedesignDone} />
      ) : (
        <div className="p-6">
          {/* PM Control */}
          <div className="mb-4">
            <PMControl projectId={projectId} />
          </div>

          {/* Stats bar */}
          <BoardStats projectName={project?.name} />

          {/* Kanban board */}
          <KanbanBoard columns={columns} onTaskClick={(task: Task) => setSelectedTask(task)} />

          {/* Task detail modal */}
          {selectedTask && <TaskDetail task={selectedTask as Task} onClose={() => setSelectedTask(null)} />}
        </div>
      )}
    </>
  )
}
