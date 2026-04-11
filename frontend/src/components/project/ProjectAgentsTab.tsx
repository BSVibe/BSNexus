import { useQuery } from '@tanstack/react-query'
import { agentsApi } from '../../api/agents'
import { AGENT_STATUS_COLORS, AGENT_STATUS_FALLBACK_COLOR, AGENT_STATUS_LABELS } from '../../constants/agentStatus'

export default function ProjectAgentsTab() {
  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list(),
  })

  if (agents.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-text-tertiary gap-3">
        <span className="material-symbols-outlined text-4xl opacity-40">groups</span>
        <p className="text-sm">No agents configured</p>
        <p className="text-xs opacity-60">Create agents in the Agents page to get started</p>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {agents.map((agent) => {
          const budgetPct = agent.monthly_budget_cents && agent.monthly_budget_cents > 0
            ? Math.round((agent.current_month_spent_cents / agent.monthly_budget_cents) * 100)
            : null

          return (
            <div
              key={agent.id}
              className="bg-stitch-surface-low rounded-xl p-4 border border-stitch-outline-variant/10"
            >
              {/* Header */}
              <div className="flex items-center gap-2 mb-2">
                <div
                  className="w-2.5 h-2.5 rounded-full shrink-0"
                  style={{ backgroundColor: AGENT_STATUS_COLORS[agent.dot] || AGENT_STATUS_COLORS[agent.status] || AGENT_STATUS_FALLBACK_COLOR }}
                />
                <span className="text-sm font-bold text-text-primary truncate">{agent.name}</span>
              </div>

              {/* Role */}
              <p className="text-xs text-text-tertiary mb-2">{agent.title || agent.role}</p>

              {/* Status — show activity summary if available, else label */}
              <p className="text-xs text-text-tertiary mb-2 truncate">
                {agent.activity || AGENT_STATUS_LABELS[agent.status] || agent.status}
              </p>

              {/* Budget bar */}
              {agent.monthly_budget_cents != null && (
                <div className="flex items-center gap-2">
                  <div className="flex-1 h-1 bg-[#343439] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${Math.min(budgetPct ?? 0, 100)}%`,
                        backgroundColor: (budgetPct ?? 0) >= 90 ? '#ffb4ab' : (budgetPct ?? 0) >= 70 ? '#eab308' : '#adc6ff',
                      }}
                    />
                  </div>
                  <span className="text-[9px] text-text-tertiary whitespace-nowrap">
                    ${((agent.current_month_spent_cents || 0) / 100).toFixed(0)}/${(agent.monthly_budget_cents / 100).toFixed(0)}
                  </span>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
