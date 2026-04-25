/** Status → semantic tone mapping. Keeps Badge / StatusDot in sync with brand. */

export type Tone =
  | 'blue'
  | 'emerald'
  | 'amber'
  | 'rose'
  | 'indigo'
  | 'gray'

export const accentHex: Record<Tone, string> = {
  blue: '#3b82f6',
  emerald: '#10b981',
  amber: '#f59e0b',
  rose: '#f43f5e',
  indigo: '#6366f1',
  gray: '#5a5f7d',
}

const STATUS_TONE: Record<string, Tone> = {
  // project
  active: 'emerald',
  archived: 'gray',
  // run / request
  running: 'blue',
  open: 'emerald',
  blocked: 'rose',
  done: 'emerald',
  pending: 'gray',
  abandoned: 'gray',
  // integration
  healthy: 'emerald',
  degraded: 'amber',
  unauthorized: 'rose',
  unreachable: 'rose',
  disabled: 'gray',
  // deliverable
  draft: 'gray',
  ready: 'amber',
  delivered: 'emerald',
}

export function statusTone(status: string | null | undefined): Tone {
  if (!status) return 'gray'
  return STATUS_TONE[status] ?? 'gray'
}
