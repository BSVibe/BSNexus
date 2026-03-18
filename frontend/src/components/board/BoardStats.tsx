import { useBoardStore } from '../../stores/boardStore'
import { Badge } from '../common'

interface Props {
  projectName?: string
}

export default function BoardStats({ projectName }: Props) {
  const { stats, getBoardStats } = useBoardStore()
  const { total, done, completionRate } = getBoardStats()

  return (
    <div className="bg-bg-card rounded-lg border border-border p-4 mb-4">
      <div className="flex items-center justify-between flex-wrap gap-4">
        {projectName && (
          <h3 className="text-lg font-semibold text-text-primary">{projectName}</h3>
        )}

        <div className="flex items-center gap-6">
          <div className="text-sm text-text-secondary">
            <span className="font-medium">{total}</span> tasks
          </div>

          <div className="flex items-center gap-2">
            <div className="w-32 h-2 bg-bg-elevated rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{ width: `${completionRate}%`, backgroundColor: 'var(--status-done)' }}
              />
            </div>
            <span className="text-sm text-text-secondary">
              {done}/{total} ({Math.round(completionRate)}%)
            </span>
          </div>

          <div className="flex items-center gap-2">
            {Object.entries(stats).map(([status, count]) => (
              <div key={status} className="flex items-center gap-1" title={status}>
                <Badge color={status} label={String(count)} size="sm" />
              </div>
            ))}
          </div>

        </div>
      </div>
    </div>
  )
}
