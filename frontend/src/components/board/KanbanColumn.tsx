import type { Task } from '../../types/task'
import TaskCard from './TaskCard'

const columnStatusColors: Record<string, string> = {
  waiting: 'var(--status-waiting)',
  ready: 'var(--status-ready)',
  in_progress: 'var(--status-in-progress)',
  review: 'var(--status-review)',
  done: 'var(--status-done)',
}

interface Props {
  title: string
  status: string
  tasks: Task[]
  onTaskClick?: (task: Task) => void
}

export default function KanbanColumn({ title, status, tasks, onTaskClick }: Props) {
  const statusColor = columnStatusColors[status] || columnStatusColors.waiting

  return (
    <div className="flex-shrink-0 w-72 bg-bg-surface rounded-lg">
      <div
        className="mb-3 flex items-center justify-between rounded-t-lg border-l-4 bg-bg-elevated px-3 py-2"
        style={{ borderLeftColor: statusColor }}
      >
        <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
        <span className="text-xs text-text-tertiary">
          {tasks.length}
        </span>
      </div>
      <div className="space-y-2 max-h-[calc(100vh-16rem)] overflow-y-auto px-2 pb-2">
        {tasks.map((task) => (
          <TaskCard key={task.id} task={task} onClick={() => onTaskClick?.(task)} />
        ))}
        {tasks.length === 0 && (
          <div className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-text-muted">
            No tasks
          </div>
        )}
      </div>
    </div>
  )
}
