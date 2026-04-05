import { useEffect, useState } from 'react'
import { useAgentStore } from '../stores/agentStore'
import type { Agent, AgentCreate, AgentOrgChartNode, ExecutorType } from '../types/agent'
import Header from '../components/layout/Header'
import { settingsApi } from '../api/settings'

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

const EXECUTOR_OPTIONS: { value: ExecutorType; label: string }[] = [
  { value: 'claude_api', label: 'Claude API' },
  { value: 'claude_code', label: 'Claude Code' },
  { value: 'bsgateway', label: 'BSGateway' },
  { value: 'codex', label: 'Codex' },
  { value: 'generic_llm', label: 'Generic LLM' },
]

function OrgChartNode({ node }: { node: AgentOrgChartNode }) {
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
        className="w-56 p-4 rounded-xl bg-stitch-surface-container border border-stitch-outline-variant/15 hover:border-stitch-primary/20 transition-all group cursor-pointer"
      >
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${STATUS_COLORS[agent.status] || 'bg-gray-500'}`} />
            <span className="text-xs text-text-secondary">{agent.status}</span>
          </div>
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-stitch-primary/10 text-stitch-primary font-medium">
            {EXECUTOR_LABELS[agent.executor_type] || agent.executor_type}
          </span>
        </div>
        <h4 className="text-sm font-bold text-text-primary group-hover:text-stitch-primary transition-colors truncate">
          {agent.name}
        </h4>
        <p className="text-xs text-text-secondary truncate">{agent.role}{agent.title ? ` · ${agent.title}` : ''}</p>
        {budgetPct !== null && (
          <div className="mt-3">
            <div className="flex justify-between text-[10px] text-text-secondary mb-1">
              <span>Budget</span>
              <span>${(agent.current_month_spent_cents / 100).toFixed(0)} / ${(agent.monthly_budget_cents! / 100).toFixed(0)}</span>
            </div>
            <div className="h-1 w-full bg-stitch-surface-highest rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${budgetPct >= 80 ? 'bg-yellow-500' : 'bg-stitch-primary'}`}
                style={{ width: `${Math.min(budgetPct, 100)}%` }}
              />
            </div>
          </div>
        )}
      </button>

      {node.children.length > 0 && (
        <>
          <div className="w-px h-6 bg-stitch-outline-variant/30" />
          <div className="flex gap-6">
            {node.children.map((child) => (
              <OrgChartNode key={child.agent.id} node={child} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function HireAgentModal({ onClose, agents, defaultExecutorType }: { onClose: () => void; agents: Agent[]; defaultExecutorType: string }) {
  const { createAgent, fetchOrgChart, fetchAgents } = useAgentStore()
  const [form, setForm] = useState<AgentCreate>({
    name: '',
    role: '',
    title: '',
    executor_type: defaultExecutorType as ExecutorType,
    capabilities: ['general'],
    job_description: '',
  })
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!form.name.trim() || !form.role.trim()) {
      setError('Name and Role are required')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      await createAgent(form)
      await fetchOrgChart()
      await fetchAgents()
      onClose()
    } catch (e) {
      setError((e as Error).message || 'Failed to create agent')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-stitch-surface-low rounded-xl w-full max-w-lg p-6 border border-stitch-outline-variant/20"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center mb-6">
          <h3 className="text-lg font-bold text-text-primary">Hire New Agent</h3>
          <button onClick={onClose} className="text-text-secondary hover:text-text-primary">✕</button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-xs uppercase tracking-widest text-text-secondary mb-1">Name *</label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="e.g. Alex, Bot-1"
              className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/30 rounded-lg text-sm text-text-primary focus:border-stitch-primary focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-widest text-text-secondary mb-1">Role *</label>
            <input
              type="text"
              value={form.role}
              onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
              placeholder="e.g. engineer, marketer, cto"
              className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/30 rounded-lg text-sm text-text-primary focus:border-stitch-primary focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-widest text-text-secondary mb-1">Title</label>
            <input
              type="text"
              value={form.title || ''}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              placeholder="e.g. Senior Engineer"
              className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/30 rounded-lg text-sm text-text-primary focus:border-stitch-primary focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-widest text-text-secondary mb-1">Report To</label>
            <select
              value={form.parent_agent_id || ''}
              onChange={(e) => setForm((f) => ({ ...f, parent_agent_id: e.target.value || undefined }))}
              className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/30 rounded-lg text-sm text-text-primary focus:border-stitch-primary focus:outline-none"
            >
              <option value="">None (top-level)</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name} ({a.role})</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs uppercase tracking-widest text-text-secondary mb-1">Job Description</label>
            <textarea
              value={form.job_description || ''}
              onChange={(e) => setForm((f) => ({ ...f, job_description: e.target.value }))}
              placeholder="Describe what this agent does..."
              rows={3}
              className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/30 rounded-lg text-sm text-text-primary focus:border-stitch-primary focus:outline-none resize-none"
            />
          </div>

          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="text-xs text-text-secondary hover:text-text-primary transition-colors"
          >
            {showAdvanced ? '▾ Hide advanced' : '▸ Advanced (executor override)'}
          </button>
          {showAdvanced && (
            <div>
              <label className="block text-xs uppercase tracking-widest text-text-secondary mb-1">
                Executor <span className="normal-case">(default: {EXECUTOR_LABELS[defaultExecutorType] || defaultExecutorType})</span>
              </label>
              <select
                value={form.executor_type}
                onChange={(e) => setForm((f) => ({ ...f, executor_type: e.target.value as ExecutorType }))}
                className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/30 rounded-lg text-sm text-text-primary focus:border-stitch-primary focus:outline-none"
              >
                {EXECUTOR_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
          )}

          {error && <p className="text-sm text-red-400">{error}</p>}

          <div className="flex gap-3 pt-2">
            <button onClick={onClose} className="flex-1 px-4 py-2 rounded-lg bg-stitch-surface-highest text-text-secondary text-sm hover:bg-stitch-outline-variant transition-colors">
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={submitting}
              className="flex-1 px-4 py-2 rounded-lg bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container font-bold text-sm hover:opacity-90 transition-all disabled:opacity-50"
            >
              {submitting ? 'Creating...' : 'Hire Agent'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function AgentsPage() {
  const { orgChart, fetchOrgChart, loading, selectedAgent, selectAgent, agents, fetchAgents, deleteAgent } = useAgentStore()
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [defaultExecutorType, setDefaultExecutorType] = useState('claude_api')

  useEffect(() => {
    fetchOrgChart()
    fetchAgents()
    settingsApi.get().then((s) => setDefaultExecutorType(s.default_executor_type)).catch(() => {})
  }, [fetchOrgChart, fetchAgents])

  const handleDelete = async (id: string) => {
    if (!confirm('Remove this agent?')) return
    await deleteAgent(id)
    selectAgent(null)
    await fetchOrgChart()
    await fetchAgents()
  }

  return (
    <>
      <Header
        title="Agent Organization"
        action={
          <button
            onClick={() => setShowCreateModal(true)}
            className="bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container px-4 py-1.5 rounded-md text-sm font-bold shadow-lg shadow-stitch-primary/20 hover:opacity-90 transition-opacity"
          >
            + Hire Agent
          </button>
        }
      />
      <div className="p-8">
      <p className="text-sm text-text-secondary mb-6">
        {agents.length} agents · {agents.filter((a) => a.status === 'online').length} online
      </p>

      {/* Org Chart */}
      {loading ? (
        <div className="flex items-center justify-center h-64 text-text-secondary">Loading org chart...</div>
      ) : orgChart.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-64 text-text-secondary">
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
        <div data-testid="agent-detail-sidebar" className="fixed right-0 top-0 w-96 h-full bg-stitch-surface-low border-l border-stitch-outline-variant/20 p-6 overflow-y-auto z-50">
          <div className="flex justify-between items-center mb-6">
            <h3 className="text-lg font-bold">{selectedAgent.name}</h3>
            <button onClick={() => selectAgent(null)} className="text-text-secondary hover:text-text-primary">✕</button>
          </div>
          <div className="space-y-4">
            <div>
              <label className="text-[10px] uppercase tracking-widest text-text-secondary">Role</label>
              <p className="text-sm">{selectedAgent.role}</p>
            </div>
            {selectedAgent.title && (
              <div>
                <label className="text-[10px] uppercase tracking-widest text-text-secondary">Title</label>
                <p className="text-sm">{selectedAgent.title}</p>
              </div>
            )}
            <div>
              <label className="text-[10px] uppercase tracking-widest text-text-secondary">Executor</label>
              <p className="text-sm">{EXECUTOR_LABELS[selectedAgent.executor_type] || selectedAgent.executor_type}</p>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-widest text-text-secondary">Capabilities</label>
              <div className="flex flex-wrap gap-1 mt-1">
                {selectedAgent.capabilities.map((cap) => (
                  <span key={cap} className="text-xs px-2 py-0.5 rounded-full bg-stitch-secondary-container text-stitch-on-secondary-container">{cap}</span>
                ))}
              </div>
            </div>
            {selectedAgent.heartbeat_enabled && (
              <div>
                <label className="text-[10px] uppercase tracking-widest text-text-secondary">Heartbeat</label>
                <p className="text-sm">Every {(selectedAgent.heartbeat_interval_seconds || 0) / 3600}h</p>
              </div>
            )}
            {selectedAgent.job_description && (
              <div>
                <label className="text-[10px] uppercase tracking-widest text-text-secondary">Job Description</label>
                <p className="text-sm text-text-secondary whitespace-pre-wrap">{selectedAgent.job_description}</p>
              </div>
            )}
            {selectedAgent.monthly_budget_cents != null && (
              <div>
                <label className="text-[10px] uppercase tracking-widest text-text-secondary">Budget</label>
                <p className="text-sm">
                  ${(selectedAgent.current_month_spent_cents / 100).toFixed(2)} / ${(selectedAgent.monthly_budget_cents / 100).toFixed(2)}
                </p>
              </div>
            )}
            <div className="pt-4 border-t border-stitch-outline-variant/20">
              <button
                onClick={() => handleDelete(selectedAgent.id)}
                className="w-full px-4 py-2 rounded-lg bg-red-500/10 text-red-400 text-sm hover:bg-red-500/20 transition-colors"
              >
                Remove Agent
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && <HireAgentModal onClose={() => setShowCreateModal(false)} agents={agents} defaultExecutorType={defaultExecutorType} />}
      </div>
    </>
  )
}
