/**
 * Canonical agent status colours — single source of truth.
 *
 * Every component that renders an agent dot uses these maps:
 * - Plan view AgentStatusBar
 * - ProjectAgentsTab
 * - OrgChart node cards
 * - MentionAutocomplete
 *
 * Keys cover both ``Agent.status`` (online/busy/offline/budget_exceeded)
 * and ``Agent.dot`` (green/yellow/red/gray/blue) so any component can
 * look up by whichever field it has.
 */

/** Hex colours keyed by status or dot value. */
export const AGENT_STATUS_COLORS: Record<string, string> = {
  // By Agent.status
  online: '#fbbf24', // amber-400 — idle
  busy: '#34d399', // emerald-400 — working
  offline: '#ef4444', // rose-500 — offline
  budget_exceeded: '#6b7280', // gray-500
  // By Agent.dot
  green: '#34d399', // emerald-400 — working/running
  blue: '#34d399', // emerald-400 — chatting (same as green)
  yellow: '#fbbf24', // amber-400 — idle
  red: '#ef4444', // rose-500 — offline/blocked
  gray: '#ef4444', // rose-500 — offline
}

/** Optional glow (box-shadow) for active states. */
export const AGENT_STATUS_GLOW: Record<string, string> = {
  green: '0 0 8px rgba(52,211,153,0.6)',
  blue: '0 0 8px rgba(52,211,153,0.6)',
  red: '0 0 8px rgba(244,63,94,0.6)',
  gray: '',
  yellow: '',
  busy: '0 0 8px rgba(52,211,153,0.6)',
  online: '',
  offline: '',
  budget_exceeded: '',
}

/** Human-readable labels keyed by status or dot value. */
export const AGENT_STATUS_LABELS: Record<string, string> = {
  // By Agent.status
  online: 'idle',
  busy: 'working',
  offline: 'offline',
  budget_exceeded: 'budget exceeded',
  // By Agent.dot
  green: 'running',
  blue: 'working',
  yellow: 'idle',
  red: 'blocked',
  gray: 'offline',
}

/** Fallback colour when status is not in the map. */
export const AGENT_STATUS_FALLBACK_COLOR = '#ef4444'
