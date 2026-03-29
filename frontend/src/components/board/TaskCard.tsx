import type { Task } from '../../types/task'
import { Badge } from '../common'
import { Bug, Link2, Zap, TestTube, Wrench, RefreshCw, Sparkles } from 'lucide-react'

const typeConfig: Record<string, { icon: typeof Bug; color: string }> = {
  bug: { icon: Bug, color: '#EF4444' },
  feature: { icon: Sparkles, color: '#3B82F6' },
  improvement: { icon: Zap, color: '#8B5CF6' },
  test: { icon: TestTube, color: '#10B981' },
  chore: { icon: Wrench, color: '#6B7280' },
  refactor: { icon: RefreshCw, color: '#F59E0B' },
}

interface Props {
  task: Task
  onClick?: () => void
}

export default function TaskCard({ task, onClick }: Props) {
  const isBug = task.task_type === 'bug'
  const isActive = task.status === 'in_progress'
  const typeInfo = typeConfig[task.task_type] || typeConfig.feature
  const TypeIcon = typeInfo.icon

  const borderLeftColor = isBug
    ? '#EF4444'
    : isActive
      ? 'rgb(var(--color-accent))'
      : undefined

  return (
    <div
      onClick={onClick}
      className="group cursor-pointer rounded-lg border border-border/40 bg-bg-card p-3 hover:bg-bg-hover hover:border-accent/30 transition-all duration-150 hover:shadow-lg hover:shadow-black/20"
      style={borderLeftColor ? { borderLeftWidth: '2px', borderLeftColor } : undefined}
    >
      {/* Title */}
      <h4 className="text-[13px] font-medium text-text-primary leading-snug line-clamp-2 mb-2 group-hover:text-white transition-colors">
        {task.title}
      </h4>

      {/* Metadata row */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span
          className="inline-flex items-center gap-1 text-[11px] font-medium px-1.5 py-0.5 rounded"
          style={{
            backgroundColor: `color-mix(in srgb, ${typeInfo.color} 12%, transparent)`,
            color: typeInfo.color,
          }}
        >
          <TypeIcon size={10} />
          {task.task_type}
        </span>
        <Badge color={task.priority} label={task.priority} size="sm" />
        {task.depends_on.length > 0 && (
          <span
            className="inline-flex items-center gap-0.5 text-[11px] text-text-tertiary"
            title={`Depends on ${task.depends_on.length} task(s)`}
          >
            <Link2 size={10} />
            {task.depends_on.length}
          </span>
        )}
        {task.retry_count > 0 && (
          <span className="inline-flex items-center gap-0.5 text-[11px] text-amber-500" title={`${task.retry_count} retries`}>
            <RefreshCw size={10} />
            {task.retry_count}
          </span>
        )}
      </div>
    </div>
  )
}
