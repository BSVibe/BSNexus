import { useQuery } from '@tanstack/react-query'
import { planTreeApi, type AgentDot, type AgentStatusCard } from '../../api/planTree'
import { usePlanStore } from '../../stores/planStore'

const DOT_CLASSES: Record<AgentDot, string> = {
  green: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]',
  blue: 'bg-sky-400 shadow-[0_0_8px_rgba(56,189,248,0.6)]',
  yellow: 'bg-amber-400',
  red: 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.6)]',
  gray: 'bg-stitch-outline-variant/40',
}

const DOT_LABELS: Record<AgentDot, string> = {
  green: 'running',
  blue: 'thinking',
  yellow: 'idle',
  red: 'blocked',
  gray: 'offline',
}

interface AgentStatusBarProps {
  projectId: string
}

export default function AgentStatusBar({ projectId }: AgentStatusBarProps) {
  const { data: agents = [], isLoading } = useQuery({
    queryKey: ['agent-status', projectId],
    queryFn: () => planTreeApi.getAgentStatus(projectId),
    enabled: !!projectId,
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
            key={agent.agent_id}
            agent={agent}
            highlighted={highlightedAgentId === agent.agent_id}
            onToggle={() => setHighlightedAgent(agent.agent_id)}
          />
        ))}
      </div>
    </div>
  )
}

interface AgentCardProps {
  agent: AgentStatusCard
  highlighted: boolean
  onToggle: () => void
}

function AgentCard({ agent, highlighted, onToggle }: AgentCardProps) {
  const dotClass = DOT_CLASSES[agent.dot]
  const dotLabel = DOT_LABELS[agent.dot]

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
          {agent.current_task ? agent.current_task.title : dotLabel}
        </span>
      </div>
    </button>
  )
}
