import type { Task } from '../../types/task'
import TaskCard from './TaskCard'
import { Inbox } from 'lucide-react'

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
    <div className="flex-1 min-w-[240px] max-w-[320px] flex flex-col bg-bg-surface/60 rounded-xl border border-border/40 overflow-hidden">
      {/* Top accent bar */}
      <div
        className="h-[3px] shrink-0"
        style={{ background: `linear-gradient(90deg, ${statusColor}, transparent)` }}
      />

      {/* Column header */}
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div
            className="w-2 h-2 rounded-full"
            style={{ backgroundColor: statusColor }}
          />
          <h3 className="text-[13px] font-semibold text-text-primary tracking-tight">{title}</h3>
        </div>
        <span
          className="text-[11px] font-semibold tabular-nums min-w-[22px] text-center px-1.5 py-0.5 rounded-md"
          style={{
            backgroundColor: `color-mix(in srgb, ${statusColor} 12%, transparent)`,
            color: statusColor,
          }}
        >
          {tasks.length}
        </span>
      </div>

      {/* Divider */}
      <div className="mx-3 border-t border-border/30" />

      {/* Task list */}
      <div className="flex-1 space-y-2 max-h-[calc(100vh-18rem)] overflow-y-auto p-2.5">
        {tasks.map((task) => (
          <TaskCard key={task.id} task={task} onClick={() => onTaskClick?.(task)} />
        ))}
        {tasks.length === 0 && (
          <div className="flex flex-col items-center justify-center py-10 text-text-muted">
            <Inbox size={20} className="mb-2 opacity-40" />
            <span className="text-xs">No tasks</span>
          </div>
        )}
      </div>
    </div>
  )
}
