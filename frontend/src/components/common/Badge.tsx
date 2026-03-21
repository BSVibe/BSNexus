const statusColorMap: Record<string, string> = {
  waiting: 'var(--status-waiting)',
  ready: 'var(--status-ready)',
  in_progress: 'var(--status-in-progress)',
  review: 'var(--status-review)',
  done: 'var(--status-done)',
  redesign: 'var(--status-redesign)',
  // Priority colors
  critical: '#EF4444',
  high: '#F97316',
  medium: '#3B82F6',
  low: '#6B7280',
  // Worker status
  idle: '#22C55E',
  busy: '#F59E0B',
  offline: '#6B7280',
}

interface BadgeProps {
  color: string
  label: string
  size?: 'sm' | 'md'
}

const sizeClasses = {
  sm: { dot: 'w-1.5 h-1.5', text: 'text-xs', gap: 'gap-1.5', px: 'px-2 py-0.5' },
  md: { dot: 'w-2 h-2', text: 'text-sm', gap: 'gap-2', px: 'px-2.5 py-1' },
}

export function Badge({ color, label, size = 'sm' }: BadgeProps) {
  const resolvedColor = statusColorMap[color] || color
  const s = sizeClasses[size]

  return (
    <span className={`inline-flex items-center ${s.gap} ${s.px} rounded-full bg-bg-elevated ${s.text} text-text-secondary`}>
      <span
        className={`${s.dot} rounded-full shrink-0`}
        style={{ backgroundColor: resolvedColor }}
      />
      {label}
    </span>
  )
}

export type { BadgeProps }
