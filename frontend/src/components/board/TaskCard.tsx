import type { Task } from '../../types/task'

const typeIcons: Record<string, string> = {
  bug: 'bug_report',
  feature: 'auto_awesome',
  improvement: 'bolt',
  test: 'science',
  chore: 'build',
  refactor: 'sync',
}

const priorityColors: Record<string, string> = {
  critical: 'text-stitch-error',
  high: 'text-stitch-tertiary',
  medium: 'text-stitch-primary',
  low: 'text-text-tertiary',
}

interface Props {
  task: Task
  onClick?: () => void
  isDone?: boolean
}

export default function TaskCard({ task, onClick, isDone }: Props) {
  const isActive = task.status === 'in_progress'
  const isBug = task.task_type === 'bug'
  const icon = typeIcons[task.task_type] || 'task'
  const priorityClass = priorityColors[task.priority] || 'text-text-tertiary'

  return (
    <div
      onClick={onClick}
      className={`group cursor-pointer bg-stitch-surface-container p-4 rounded-lg hover:bg-stitch-surface-high transition-colors ${
        isActive
          ? 'border-l-4 border-stitch-primary shadow-[inset_0_0_10px_rgba(173,198,255,0.05)] ring-1 ring-stitch-primary/10'
          : isBug
            ? 'border-l-4 border-stitch-error'
            : 'border-l-4 border-transparent'
      }`}
    >
      <div className="flex justify-between items-start mb-2">
        <h4 className={`text-sm font-semibold leading-snug ${isDone ? 'line-through text-text-secondary' : ''}`}>
          {task.title}
        </h4>
        {isActive && (
          <span className="material-symbols-outlined text-stitch-primary text-sm animate-spin">sync</span>
        )}
        {isDone && (
          <span className="material-symbols-outlined text-stitch-primary text-sm" style={{ fontVariationSettings: "'FILL' 1" }}>check_circle</span>
        )}
        {!isActive && !isDone && (
          <span className="material-symbols-outlined text-text-muted text-sm opacity-0 group-hover:opacity-100 transition-opacity">more_horiz</span>
        )}
      </div>

      {/* Progress bar for active tasks */}
      {isActive && (
        <div className="w-full bg-stitch-surface-lowest h-1 rounded-full mb-4 overflow-hidden">
          <div className="bg-stitch-primary h-full w-[65%]" />
        </div>
      )}

      {/* Tags */}
      <div className="flex flex-wrap gap-2 mb-4">
        <span className="px-2 py-0.5 rounded-full bg-stitch-secondary-container text-stitch-on-secondary-container text-[10px] font-bold">
          {task.task_type}
        </span>
        {task.depends_on.length > 0 && (
          <span className="px-2 py-0.5 rounded-full bg-stitch-surface-highest text-text-secondary text-[10px] font-medium flex items-center gap-1">
            <span className="material-symbols-outlined" style={{ fontSize: '12px' }}>link</span>
            {task.depends_on.length}
          </span>
        )}
        {task.retry_count > 0 && (
          <span className="px-2 py-0.5 rounded-full bg-stitch-error-container/20 text-stitch-error text-[10px] font-bold flex items-center gap-1">
            <span className="material-symbols-outlined" style={{ fontSize: '12px' }}>refresh</span>
            {task.retry_count}
          </span>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-text-tertiary" style={{ fontSize: '16px' }}>{icon}</span>
          <span className="text-[10px] font-medium text-text-secondary">{task.phase_id.slice(0, 8)}</span>
        </div>
        <span className={`text-[10px] font-bold ${priorityClass}`}>
          {task.priority.toUpperCase()}
        </span>
      </div>
    </div>
  )
}
