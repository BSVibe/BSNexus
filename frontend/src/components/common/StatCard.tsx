interface StatCardProps {
  label: string
  value: string | number
  subtext?: string
  icon?: string
  badge?: { color: string; label: string }
}

export function StatCard({ label, value, subtext, icon }: StatCardProps) {
  return (
    <div className="bg-stitch-surface-low p-5 rounded-xl flex flex-col justify-between h-32 border border-stitch-outline-variant/10">
      <span className="text-xs uppercase tracking-widest text-text-secondary font-bold">{label}</span>
      <div className="flex items-end justify-between">
        <span className="text-3xl font-extrabold tracking-tighter text-stitch-primary">{value}</span>
        {icon && (
          <span className="material-symbols-outlined text-stitch-primary/40 text-4xl">{icon}</span>
        )}
        {subtext && (
          <span className="text-xs text-text-tertiary font-bold">{subtext}</span>
        )}
      </div>
    </div>
  )
}

export type { StatCardProps }
