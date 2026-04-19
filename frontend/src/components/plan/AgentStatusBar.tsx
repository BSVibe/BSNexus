import { useQuery } from '@tanstack/react-query'
import { agentsApi } from '../../api/agents'
import type { Agent } from '../../types/agent'
import {
  AGENT_STATUS_COLORS,
  AGENT_STATUS_FALLBACK_COLOR,
  AGENT_STATUS_GLOW,
  AGENT_STATUS_LABELS,
} from '../../constants/agentStatus'
import { useAgentProcessingStore } from '../../stores/agentProcessingStore'
import { usePlanStore } from '../../stores/planStore'

export default function AgentStatusBar({ projectId }: { projectId?: string }) {
  const { data: agents = [], isLoading } = useQuery({
    queryKey: ['agents', projectId],
    queryFn: () => agentsApi.list(projectId),
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
  const processingInfo = useAgentProcessingStore(
    (s) => s.processingAgents.get(agent.id)
  )
  const isProcessing = !!processingInfo
  const dot = isProcessing ? 'green' : (agent.dot || 'gray')
  const currentTask = agent.current_task
  const activity = agent.activity
  const color = AGENT_STATUS_COLORS[dot] || AGENT_STATUS_FALLBACK_COLOR
  const glow = AGENT_STATUS_GLOW[dot] || ''
  const dotLabel = AGENT_STATUS_LABELS[dot] || dot

  const subtitle = isProcessing
    ? (processingInfo?.activity || '처리 중...')
    : currentTask
      ? currentTask.title
      : activity || dotLabel

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
      <span
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ backgroundColor: color, boxShadow: glow || undefined }}
      />
      <div className="min-w-0 flex flex-col">
        <span className="text-xs font-bold text-text-primary truncate">{agent.name}</span>
        <span className="text-[10px] text-text-tertiary truncate max-w-[180px]" title={subtitle}>
          {subtitle}
        </span>
      </div>
    </button>
  )
}
