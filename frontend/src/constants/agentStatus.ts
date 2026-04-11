/**
 * Canonical agent status colours — single source of truth.
 *
 * Used by:
 * - Plan view AgentStatusBar (dot classes)
 * - ProjectAgentsTab (inline style)
 * - OrgChart node cards (inline style)
 * - MentionAutocomplete (inline style)
 *
 * The values correspond to the backend ``resolve_agent_runtime_status``
 * output: ``online`` | ``busy`` | ``offline`` | ``budget_exceeded``.
 * Plan view has its own dot resolver that maps task-level states (running,
 * blocked) to emerald / rose independently.
 */

/** Hex colours keyed by ``Agent.status``. */
export const AGENT_STATUS_COLORS: Record<string, string> = {
  // By Agent.status value
  online: '#fbbf24', // amber-400 — idle
  busy: '#34d399', // emerald-400 — working
  offline: '#6b7280', // gray-500
  budget_exceeded: '#ef4444', // rose-500
  // By Agent.dot value (backend resolve_agent_status_dot)
  green: '#34d399', // emerald-400 — working/running
  yellow: '#fbbf24', // amber-400 — idle
  red: '#ef4444', // rose-500 — blocked
  gray: '#6b7280', // gray-500 — offline
}

/** Human labels keyed by ``Agent.status``. */
export const AGENT_STATUS_LABELS: Record<string, string> = {
  online: 'idle',
  busy: 'working',
  offline: 'offline',
  budget_exceeded: 'budget exceeded',
}

/** Fallback colour when status is not in the map. */
export const AGENT_STATUS_FALLBACK_COLOR = '#6b7280'
