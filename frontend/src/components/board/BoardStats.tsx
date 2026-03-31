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
    <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
      <div className="bg-stitch-surface-low p-5 rounded-xl flex flex-col justify-between h-32 border border-stitch-outline-variant/10">
        <span className="text-xs uppercase tracking-widest text-text-secondary font-bold">Completion</span>
        <div className="flex items-end justify-between">
          <span className="text-3xl font-extrabold tracking-tighter text-stitch-primary">{Math.round(completionRate)}%</span>
          <span className="material-symbols-outlined text-stitch-primary/40 text-4xl">bolt</span>
        </div>
      </div>
      <div className="bg-stitch-surface-low p-5 rounded-xl flex flex-col justify-between h-32 border border-stitch-outline-variant/10">
        <span className="text-xs uppercase tracking-widest text-text-secondary font-bold">Total Tasks</span>
        <div className="flex items-end justify-between">
          <span className="text-3xl font-extrabold tracking-tighter">{total}</span>
          <span className="text-xs text-stitch-primary font-bold">{done} done</span>
        </div>
      </div>
      <div className="bg-stitch-surface-low p-5 rounded-xl flex flex-col justify-between h-32 border border-stitch-outline-variant/10">
        <span className="text-xs uppercase tracking-widest text-text-secondary font-bold">Status Breakdown</span>
        <div className="flex items-center gap-1.5 flex-wrap">
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
      <div className="bg-stitch-surface-low p-5 rounded-xl flex flex-col justify-between h-32 border border-stitch-outline-variant/10">
        <span className="text-xs uppercase tracking-widest text-text-secondary font-bold">Project Status</span>
        <div className="flex items-end justify-between">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-stitch-primary animate-pulse" />
            <span className="text-xl font-bold">{projectName || 'Active'}</span>
          </div>
          <span className="material-symbols-outlined text-text-secondary">cloud_done</span>
        </div>
      </div>
    </div>
  )
}
