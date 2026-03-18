import type { Task } from '../../types/task'
import { Badge } from '../common'
import { Bug } from 'lucide-react'

interface Props {
  task: Task
  onClick?: () => void
}

export default function TaskCard({ task, onClick }: Props) {
  const isBug = task.task_type === 'bug'

  return (
    <div
      onClick={onClick}
      className={`cursor-pointer rounded-lg border bg-bg-card p-3 hover:bg-bg-hover transition-colors ${
        isBug ? 'border-l-2 border-l-red-500 border-t-border border-r-border border-b-border' : 'border-border'
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-1.5">
          {isBug && <Bug size={14} className="text-red-500 shrink-0" />}
          <h4 className="text-sm font-medium text-text-primary leading-snug">{task.title}</h4>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <Badge color={task.priority} label={task.priority} size="sm" />
        {task.depends_on.length > 0 && (
          <span
            className="inline-flex items-center gap-0.5 text-xs text-text-tertiary"
            title={`Depends on ${task.depends_on.length} task(s)`}
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"
              />
            </svg>
            {task.depends_on.length}
          </span>
        )}
      </div>
    </div>
  )
}
