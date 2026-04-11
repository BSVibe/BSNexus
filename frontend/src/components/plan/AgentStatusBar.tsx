import { useQuery } from '@tanstack/react-query'
import { agentsApi } from '../../api/agents'
import type { Agent } from '../../types/agent'
import { usePlanStore } from '../../stores/planStore'

// The dot field now comes directly from the /agents API response
// (single source of truth). These classes just map the value to Tailwind.
const DOT_CLASSES: Record<string, string> = {
  green: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]',
  blue: 'bg-sky-400 shadow-[0_0_8px_rgba(56,189,248,0.6)]',
  yellow: 'bg-amber-400',
  red: 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.6)]',
  gray: 'bg-stitch-outline-variant/40',
}

const DOT_LABELS: Record<string, string> = {
  green: 'running',
  blue: 'thinking',
  yellow: 'idle',
  red: 'blocked',
  gray: 'offline',
}

export default function AgentStatusBar() {
  // Use the SAME query key + API as Agents tab / OrgChart / chat sidebar.
  // Previously this was a separate ['agent-status', projectId] endpoint
  // which had its own status resolver and constantly disagreed with the
  // Agents tab. Now both read ['agents'] and the backend populates
  // dot + current_task on every AgentResponse.
  const { data: agents = [], isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list(),
    refetchInterval: 30000,
  })
  const highlightedAgentId = usePlanStore((s) => s.highlightedAgentId)
  const setHighlightedAgent = usePlanStore((s) => s.setHighlightedAgent)

  if (isLoading) {
    return (
      <div className="px-6 py-3 border-b border-stitch-outline-variant/10 text-text-tertiary text-xs">
        Loading agents…
      </div>
    )
  }

  if (agents.length === 0) {
    return (
      <div className="px-6 py-3 border-b border-stitch-outline-variant/10 text-text-tertiary text-xs">
        No active agents — create one in the Agents tab to get started.
      </div>
    )
  }

  return (
    <div className="px-4 py-3 border-b border-stitch-outline-variant/10 bg-stitch-surface">
      <div className="flex flex-wrap gap-2 max-h-24 overflow-y-auto">
        {agents.map((agent) => (
          <AgentCard
            key={agent.id}
            agent={agent}
            highlighted={highlightedAgentId === agent.id}
            onToggle={() => setHighlightedAgent(agent.id)}
          />
        ))}
      </div>
    </div>
  )
}

interface AgentCardProps {
  agent: Agent
  highlighted: boolean
  onToggle: () => void
}

function AgentCard({ agent, highlighted, onToggle }: AgentCardProps) {
  const dot = agent.dot || 'gray'
  const currentTask = agent.current_task
  const dotClass = DOT_CLASSES[dot] || DOT_CLASSES.gray
  const dotLabel = DOT_LABELS[dot] || dot

  return (
    <button
      type="button"
      onClick={onToggle}
      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors ${
        highlighted
          ? 'border-stitch-primary bg-stitch-primary/10'
          : 'border-stitch-outline-variant/20 bg-stitch-surface-low hover:border-stitch-outline-variant/40'
      }`}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`} />
      <div className="min-w-0 flex flex-col">
        <span className="text-xs font-bold text-text-primary truncate">{agent.name}</span>
        <span className="text-[10px] text-text-tertiary truncate">
          {currentTask ? currentTask.title : dotLabel}
        </span>
      </div>
    </button>
  )
}
