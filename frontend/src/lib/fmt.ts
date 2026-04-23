/** Time + id formatters used across the app. */

export function relTime(iso: string): string {
  const d = new Date(iso)
  const s = (Date.now() - d.getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d ago`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function truncId(id: string | null | undefined): string {
  if (!id) return ''
  const parts = id.split('_')
  if (parts.length > 1) {
    const tail = parts[parts.length - 1]
    return `${parts[0]}_${tail.slice(0, 8)}`
  }
  // Plain UUID (no prefix) — just take the first 8 chars.
  return id.replace(/-/g, '').slice(0, 8)
}
