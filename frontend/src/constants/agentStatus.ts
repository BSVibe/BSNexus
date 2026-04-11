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
  online: '#fbbf24', // amber-400 — idle
  busy: '#38bdf8', // sky-400 — thinking
  offline: '#6b7280', // gray-500
  budget_exceeded: '#ef4444', // rose-500
}

/** Human labels keyed by ``Agent.status``. */
export const AGENT_STATUS_LABELS: Record<string, string> = {
  online: 'idle',
  busy: 'thinking',
  offline: 'offline',
  budget_exceeded: 'budget exceeded',
}

/** Fallback colour when status is not in the map. */
export const AGENT_STATUS_FALLBACK_COLOR = '#6b7280'
