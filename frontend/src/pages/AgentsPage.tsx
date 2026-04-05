import { useEffect, useState } from 'react'
import { useAgentStore } from '../stores/agentStore'
import type { AgentOrgChartNode } from '../types/agent'

const STATUS_COLORS: Record<string, string> = {
  online: 'bg-green-500',
  busy: 'bg-yellow-500',
  offline: 'bg-gray-500',
  budget_exceeded: 'bg-red-500',
}

const EXECUTOR_LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  claude_api: 'Claude API',
  bsgateway: 'BSGateway',
  codex: 'Codex',
  generic_llm: 'Generic LLM',
}

function OrgChartNode({ node, depth = 0 }: { node: AgentOrgChartNode; depth?: number }) {
  const { selectAgent } = useAgentStore()
  const agent = node.agent
  const budgetPct =
    agent.monthly_budget_cents && agent.monthly_budget_cents > 0
      ? Math.round((agent.current_month_spent_cents / agent.monthly_budget_cents) * 100)
      : null

  return (
    <div className="flex flex-col items-center">
      <button
        onClick={() => selectAgent(agent)}
        className="w-56 p-4 rounded-xl bg-[#1f1f24] border border-[#424754]/15 hover:border-[#85adff]/20 transition-all group cursor-pointer"
      >
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${STATUS_COLORS[agent.status] || 'bg-gray-500'}`} />
            <span className="text-xs text-[#abaab0]">{agent.status}</span>
          </div>
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#85adff]/10 text-[#85adff] font-medium">
            {EXECUTOR_LABELS[agent.executor_type] || agent.executor_type}
          </span>
        </div>
        <h4 className="text-sm font-bold text-[#faf8fe] group-hover:text-[#85adff] transition-colors truncate">
          {agent.name}
        </h4>
        <p className="text-xs text-[#abaab0] truncate">{agent.role}{agent.title ? ` · ${agent.title}` : ''}</p>
        {budgetPct !== null && (
          <div className="mt-3">
            <div className="flex justify-between text-[10px] text-[#abaab0] mb-1">
              <span>Budget</span>
              <span>${(agent.current_month_spent_cents / 100).toFixed(0)} / ${(agent.monthly_budget_cents! / 100).toFixed(0)}</span>
            </div>
            <div className="h-1 w-full bg-[#343439] rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${budgetPct >= 80 ? 'bg-yellow-500' : 'bg-[#85adff]'}`}
                style={{ width: `${Math.min(budgetPct, 100)}%` }}
              />
            </div>
          </div>
        )}
      </button>

      {node.children.length > 0 && (
        <>
          <div className="w-px h-6 bg-[#424754]/30" />
          <div className="flex gap-6">
            {node.children.map((child) => (
              <OrgChartNode key={child.agent.id} node={child} depth={depth + 1} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

export default function AgentsPage() {
  const { orgChart, fetchOrgChart, loading, selectedAgent, selectAgent, agents, fetchAgents } = useAgentStore()
  const [_showCreateModal, setShowCreateModal] = useState(false)

  useEffect(() => {
    fetchOrgChart()
    fetchAgents()
  }, [fetchOrgChart, fetchAgents])

  return (
    <div className="min-h-screen bg-[#0d0e12] text-[#faf8fe]">
      {/* Header */}
      <div className="flex justify-between items-center mb-8">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Agent Organization</h2>
          <p className="text-sm text-[#abaab0] mt-1">
            {agents.length} agents · {agents.filter((a) => a.status === 'online').length} online
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="px-4 py-2 rounded-lg bg-gradient-to-r from-[#85adff] to-[#5391ff] text-[#002150] font-bold text-sm hover:opacity-90 transition-all"
        >
          + Hire Agent
        </button>
      </div>

      {/* Org Chart */}
      {loading ? (
        <div className="flex items-center justify-center h-64 text-[#abaab0]">Loading org chart...</div>
      ) : orgChart.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-64 text-[#abaab0]">
          <p className="text-lg mb-2">No agents yet</p>
          <p className="text-sm">Click "Hire Agent" to create your first AI team member</p>
        </div>
      ) : (
        <div className="flex justify-center overflow-x-auto pb-8">
          <div className="flex gap-8">
            {orgChart.map((node) => (
              <OrgChartNode key={node.agent.id} node={node} />
            ))}
          </div>
        </div>
      )}

      {/* Agent Detail Sidebar */}
      {selectedAgent && (
        <div data-testid="agent-detail-sidebar" className="fixed right-0 top-0 w-96 h-full bg-[#18191e] border-l border-[#424754]/20 p-6 overflow-y-auto z-50">
          <div className="flex justify-between items-center mb-6">
            <h3 className="text-lg font-bold">{selectedAgent.name}</h3>
            <button onClick={() => selectAgent(null)} className="text-[#abaab0] hover:text-[#faf8fe]">
              ✕
            </button>
          </div>
          <div className="space-y-4">
            <div>
              <label className="text-[10px] uppercase tracking-widest text-[#abaab0]">Role</label>
              <p className="text-sm">{selectedAgent.role}</p>
            </div>
            {selectedAgent.title && (
              <div>
                <label className="text-[10px] uppercase tracking-widest text-[#abaab0]">Title</label>
                <p className="text-sm">{selectedAgent.title}</p>
              </div>
            )}
            <div>
              <label className="text-[10px] uppercase tracking-widest text-[#abaab0]">Executor</label>
              <p className="text-sm">{EXECUTOR_LABELS[selectedAgent.executor_type] || selectedAgent.executor_type}</p>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-widest text-[#abaab0]">Capabilities</label>
              <div className="flex flex-wrap gap-1 mt-1">
                {selectedAgent.capabilities.map((cap) => (
                  <span key={cap} className="text-xs px-2 py-0.5 rounded-full bg-[#304671] text-[#b1c6f9]">
                    {cap}
                  </span>
                ))}
              </div>
            </div>
            {selectedAgent.heartbeat_enabled && (
              <div>
                <label className="text-[10px] uppercase tracking-widest text-[#abaab0]">Heartbeat</label>
                <p className="text-sm">Every {(selectedAgent.heartbeat_interval_seconds || 0) / 3600}h</p>
              </div>
            )}
            {selectedAgent.job_description && (
              <div>
                <label className="text-[10px] uppercase tracking-widest text-[#abaab0]">Job Description</label>
                <p className="text-sm text-[#abaab0] whitespace-pre-wrap">{selectedAgent.job_description}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
