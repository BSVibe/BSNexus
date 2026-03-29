import { useBoardStore } from '../../stores/boardStore'

const statusMeta: Record<string, { label: string; color: string }> = {
  waiting: { label: 'Waiting', color: 'var(--status-waiting)' },
  ready: { label: 'Ready', color: 'var(--status-ready)' },
  in_progress: { label: 'Active', color: 'var(--status-in-progress)' },
  review: { label: 'Review', color: 'var(--status-review)' },
  done: { label: 'Done', color: 'var(--status-done)' },
}

interface Props {
  projectName?: string
}

export default function BoardStats({ projectName }: Props) {
  const { stats, getBoardStats } = useBoardStore()
  const { total, done, completionRate } = getBoardStats()

  return (
    <div className="bg-bg-surface/60 rounded-xl border border-border/40 p-4 mb-4">
      <div className="flex items-center justify-between flex-wrap gap-4">
        {/* Left: project name + task count */}
        <div className="flex items-center gap-4">
          {projectName && (
            <h3 className="text-base font-semibold text-text-primary tracking-tight">{projectName}</h3>
          )}
          <span className="text-sm text-text-secondary">
            <span className="font-semibold text-text-primary">{total}</span> tasks
          </span>
        </div>

        {/* Right: progress + status pills */}
        <div className="flex items-center gap-5">
          {/* Progress bar */}
          <div className="flex items-center gap-2.5">
            <div className="w-28 h-1.5 bg-bg-elevated rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-700 ease-out"
                style={{
                  width: `${completionRate}%`,
                  background: 'linear-gradient(90deg, var(--status-in-progress), var(--status-done))',
                }}
              />
            </div>
            <span className="text-xs font-medium text-text-secondary tabular-nums">
              {done}/{total}
              <span className="text-text-muted ml-1">({Math.round(completionRate)}%)</span>
            </span>
          </div>

          {/* Status breakdown pills */}
          <div className="flex items-center gap-1.5">
            {Object.entries(stats).map(([status, count]) => {
              const meta = statusMeta[status]
              if (!meta || count === 0) return null
              return (
                <div
                  key={status}
                  className="flex items-center gap-1 text-[11px] font-medium px-2 py-1 rounded-md"
                  style={{
                    backgroundColor: `color-mix(in srgb, ${meta.color} 10%, transparent)`,
                    color: meta.color,
                  }}
                  title={meta.label}
                >
                  <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: meta.color }} />
                  {count}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
