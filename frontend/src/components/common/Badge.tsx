const statusColorMap: Record<string, string> = {
  waiting: 'var(--status-waiting)',
  ready: 'var(--status-ready)',
  in_progress: 'var(--status-in-progress)',
  review: 'var(--status-review)',
  done: 'var(--status-done)',
  redesign: 'var(--status-redesign)',
  // Priority colors
  critical: 'var(--color-error)',
  high: 'var(--status-waiting)',
  medium: 'var(--status-ready)',
  low: 'var(--status-blocked)',
  // Worker status
  idle: 'var(--color-success)',
  busy: 'var(--color-warning)',
  offline: 'var(--status-blocked)',
}

interface BadgeProps {
  color: string
  label: string
  size?: 'sm' | 'md'
}

export function Badge({ color, label, size = 'sm' }: BadgeProps) {
  const resolvedColor = statusColorMap[color] || color
  const isSm = size === 'sm'

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full font-bold ${
        isSm ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-xs'
      }`}
      style={{
        backgroundColor: `color-mix(in srgb, ${resolvedColor} 15%, transparent)`,
        color: resolvedColor,
      }}
    >
      {label}
    </span>
  )
}

export type { BadgeProps }
