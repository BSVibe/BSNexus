import { Badge } from './Badge'

interface StatCardProps {
  label: string
  value: string | number
  subtext?: string
  badge?: { color: string; label: string }
}

export function StatCard({ label, value, subtext, badge }: StatCardProps) {
  return (
    <div className="bg-bg-card border border-border/40 rounded-xl p-5 hover:border-accent/20 transition-colors group">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-text-tertiary">{label}</span>
        {badge && <Badge color={badge.color} label={badge.label} />}
      </div>
      <div className="text-3xl font-bold text-text-primary tracking-tight group-hover:text-accent transition-colors">{value}</div>
      {subtext && (
        <div className="text-xs text-text-muted mt-1.5">{subtext}</div>
      )}
    </div>
  )
}

export type { StatCardProps }
