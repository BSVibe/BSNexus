import { useEffect, useState } from 'react'
import { useAgentStore } from '../stores/agentStore'
import type { Agent, AgentCreate } from '../types/agent'
import Header from '../components/layout/Header'
import OrgChart from '../components/agents/OrgChart'
import { agentTemplatesApi, type OrgTemplate } from '../api/agentTemplates'
import { executorConfigsApi } from '../api/executorConfigs'
import type { ExecutorConfig } from '../types/executor'

const EXECUTOR_LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  claude_api: 'LLM API',
  bsgateway: 'BSGateway',
  codex: 'Codex',
  generic_llm: 'LLM API',
  worker: 'Worker',
}

// Capability tokens that resolve to skill prompt fragments at chat
// dispatch time. Keep in sync with backend ``CAPABILITY_TO_SKILLS``
// (backend/src/prompts/skills.py). The order here is the order shown
// in the Hire Agent form.
const SKILL_CAPABILITIES: Array<{ id: string; label: string; description: string }> = [
  { id: 'plan', label: 'Plan', description: 'Break goals into phases & tasks ([CREATE_TASK], [SET_GOAL])' },
  { id: 'analyze', label: 'Analyze', description: 'Read codebases, audit imports, surface risks' },
  { id: 'design', label: 'Design', description: "Own design/system.bsd and the .bsd screen specs" },
]
const FREEFORM_CAPABILITIES: Array<{ id: string; label: string }> = [
  { id: 'coding', label: 'Coding' },
  { id: 'writing', label: 'Writing' },
  { id: 'marketing', label: 'Marketing' },
  { id: 'research', label: 'Research' },
  { id: 'general', label: 'General' },
]

// OrgChartNode rendering moved to components/agents/OrgChart.tsx (React Flow)

function HireAgentModal({ onClose, agents, executorConfigs }: { onClose: () => void; agents: Agent[]; executorConfigs: ExecutorConfig[] }) {
  const { createAgent, fetchOrgChart, fetchAgents } = useAgentStore()
  const [form, setForm] = useState<AgentCreate>({
    name: '',
    role: '',
    title: '',
    executor_config_id: null, // null = use default
    // memory_keeping is universal (server-side); start every new agent
    // with the planning skill so they can break down tasks out of the
    // box. The user can toggle the rest before hiring.
    capabilities: ['plan', 'general'],
    job_description: '',
  })

  const toggleCapability = (id: string) => {
    setForm((f) => {
      const current = new Set(f.capabilities ?? [])
      if (current.has(id)) {
        current.delete(id)
      } else {
        current.add(id)
      }
      return { ...f, capabilities: Array.from(current) }
    })
  }
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
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-stitch-surface-low rounded-xl w-full max-w-lg p-6 border border-stitch-outline-variant/20 max-h-[90vh] overflow-y-auto"
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

          <div>
            <label className="block text-xs uppercase tracking-widest text-text-secondary mb-2">Capabilities</label>
            <p className="text-[11px] text-text-tertiary mb-2">
              Skills determine what this agent can do. <strong>Plan</strong> lets the agent break down goals
              and create <code>[DECISION]</code> markers. <strong>Analyze</strong> lets them read and audit codebases.
              <strong>Design</strong> lets them manage the design system. Memory keeping is always on.
            </p>
            <p className="text-[10px] text-amber-400/80 mb-2">
              💡 Tip: Every team should have at least one agent with <strong>Plan</strong> capability
              to make project decisions. Without it, no agent can confirm directions.
            </p>
            <div className="space-y-1.5">
              {SKILL_CAPABILITIES.map((cap) => {
                const active = (form.capabilities ?? []).includes(cap.id)
                return (
                  <label
                    key={cap.id}
                    className={`flex items-start gap-2 cursor-pointer rounded-md px-2 py-1.5 border transition-colors ${
                      active
                        ? 'border-stitch-primary/60 bg-stitch-primary/5'
                        : 'border-transparent hover:bg-stitch-surface-highest/50'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={active}
                      onChange={() => toggleCapability(cap.id)}
                      className="mt-0.5 accent-stitch-primary"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-text-primary font-medium">{cap.label}</div>
                      <div className="text-[11px] text-text-tertiary">{cap.description}</div>
                    </div>
                  </label>
                )
              })}
            </div>
            <details className="mt-2 group">
              <summary className="text-[11px] text-text-tertiary hover:text-text-secondary cursor-pointer list-none">
                <span className="group-open:hidden">▸ Other tags</span>
                <span className="hidden group-open:inline">▾ Other tags</span>
              </summary>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {FREEFORM_CAPABILITIES.map((cap) => {
                  const active = (form.capabilities ?? []).includes(cap.id)
                  return (
                    <button
                      key={cap.id}
                      type="button"
                      onClick={() => toggleCapability(cap.id)}
                      className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                        active
                          ? 'border-stitch-primary text-stitch-primary bg-stitch-primary/10'
                          : 'border-stitch-outline-variant/30 text-text-tertiary hover:text-text-secondary'
                      }`}
                    >
                      {cap.label}
                    </button>
                  )
                })}
              </div>
            </details>
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
              <label className="block text-xs uppercase tracking-widest text-text-secondary mb-1">Executor</label>
              <select
                value={form.executor_config_id ?? '_default'}
                onChange={(e) => setForm((f) => ({ ...f, executor_config_id: e.target.value === '_default' ? null : e.target.value }))}
                className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/30 rounded-lg text-sm text-text-primary focus:border-stitch-primary focus:outline-none"
              >
                <option value="_default">Use Default</option>
                {executorConfigs.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({EXECUTOR_LABELS[c.executor_type] || c.executor_type})
                    {c.is_default ? ' ★' : ''}
                  </option>
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

function AgentDetailSidebar({ agent, onClose, onDelete, executorConfigs }: { agent: Agent; onClose: () => void; onDelete: (id: string) => void; executorConfigs: ExecutorConfig[] }) {
  const { updateAgent, fetchOrgChart, fetchAgents } = useAgentStore()
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    name: agent.name,
    role: agent.role,
    title: agent.title || '',
    job_description: agent.job_description || '',
    executor_config_id: agent.executor_config_id as string | null,
    capabilities: [...agent.capabilities],
    heartbeat_enabled: agent.heartbeat_enabled,
    heartbeat_interval_seconds: agent.heartbeat_interval_seconds ?? 3600,
    monthly_budget_cents: agent.monthly_budget_cents,
  })

  useEffect(() => {
    setForm({
      name: agent.name,
      role: agent.role,
      title: agent.title || '',
      job_description: agent.job_description || '',
      executor_config_id: agent.executor_config_id,
      capabilities: [...agent.capabilities],
      heartbeat_enabled: agent.heartbeat_enabled,
      heartbeat_interval_seconds: agent.heartbeat_interval_seconds ?? 3600,
      monthly_budget_cents: agent.monthly_budget_cents,
    })
    setEditing(false)
    // Reset form only when a different agent is selected, not on every field change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id])

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateAgent(agent.id, {
        name: form.name,
        role: form.role,
        title: form.title || undefined,
        job_description: form.job_description || undefined,
        executor_config_id: form.executor_config_id,
        capabilities: form.capabilities,
        heartbeat_enabled: form.heartbeat_enabled,
        heartbeat_interval_seconds: form.heartbeat_enabled ? form.heartbeat_interval_seconds : null,
        monthly_budget_cents: form.monthly_budget_cents,
      })
      await fetchOrgChart()
      await fetchAgents()
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  const labelClass = 'text-[10px] uppercase tracking-widest text-text-secondary'
  const inputClass = 'w-full px-2.5 py-1.5 bg-stitch-surface-lowest border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary'

  return (
    <div data-testid="agent-detail-sidebar" className="fixed right-0 top-0 w-96 h-full bg-stitch-surface-low border-l border-stitch-outline-variant/20 p-6 overflow-y-auto z-50">
      <div className="flex justify-between items-center mb-6">
        <h3 className="text-lg font-bold">{agent.name}</h3>
        <div className="flex items-center gap-2">
          {!editing ? (
            <button onClick={() => setEditing(true)} className="text-text-secondary hover:text-stitch-primary transition-colors" title="Edit">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>edit</span>
            </button>
          ) : (
            <>
              <button onClick={handleSave} disabled={saving} className="text-xs px-2.5 py-1 rounded bg-stitch-primary text-stitch-on-primary font-bold hover:opacity-90 disabled:opacity-50">
                {saving ? '...' : 'Save'}
              </button>
              <button onClick={() => { setEditing(false); setForm({ name: agent.name, role: agent.role, title: agent.title || '', job_description: agent.job_description || '', executor_config_id: agent.executor_config_id as string | null, capabilities: [...agent.capabilities], heartbeat_enabled: agent.heartbeat_enabled, heartbeat_interval_seconds: agent.heartbeat_interval_seconds ?? 3600, monthly_budget_cents: agent.monthly_budget_cents }) }} className="text-xs px-2.5 py-1 rounded bg-stitch-surface-highest text-text-secondary font-bold hover:opacity-90">
                Cancel
              </button>
            </>
          )}
          <button onClick={onClose} className="text-text-secondary hover:text-text-primary">✕</button>
        </div>
      </div>
      <div className="space-y-4">
        <div>
          <label className={labelClass}>Name</label>
          {editing ? (
            <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} className={inputClass} />
          ) : (
            <p className="text-sm">{agent.name}</p>
          )}
        </div>
        <div>
          <label className={labelClass}>Role</label>
          {editing ? (
            <input value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))} className={inputClass} />
          ) : (
            <p className="text-sm">{agent.role}</p>
          )}
        </div>
        <div>
          <label className={labelClass}>Title</label>
          {editing ? (
            <input value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} placeholder="Optional title" className={inputClass} />
          ) : (
            <p className="text-sm">{agent.title || <span className="text-text-tertiary">—</span>}</p>
          )}
        </div>
        <div>
          <label className={labelClass}>Executor</label>
          {editing ? (
            <select
              value={form.executor_config_id ?? '_default'}
              onChange={(e) => setForm((f) => ({ ...f, executor_config_id: e.target.value === '_default' ? null : e.target.value }))}
              className={inputClass}
            >
              <option value="_default">Use Default</option>
              {executorConfigs.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({EXECUTOR_LABELS[c.executor_type] || c.executor_type})
                  {c.is_default ? ' ★' : ''}
                </option>
              ))}
            </select>
          ) : (
            <p className="text-sm">
              {agent.executor_config_id
                ? executorConfigs.find((c) => c.id === agent.executor_config_id)?.name || agent.executor_type
                : `Default (${EXECUTOR_LABELS[agent.executor_type] || agent.executor_type})`
              }
            </p>
          )}
        </div>
        <div>
          <label className={labelClass}>Capabilities</label>
          {editing ? (
            <div className="flex flex-wrap gap-1.5 mt-1">
              {['coding', 'writing', 'analysis', 'marketing', 'research', 'general'].map((cap) => {
                const active = form.capabilities.includes(cap)
                return (
                  <button
                    key={cap}
                    type="button"
                    onClick={() => setForm((f) => ({
                      ...f,
                      capabilities: active ? f.capabilities.filter((c) => c !== cap) : [...f.capabilities, cap],
                    }))}
                    className={`text-xs px-2 py-0.5 rounded-full transition-colors ${active ? 'bg-stitch-secondary-container text-stitch-on-secondary-container' : 'bg-stitch-surface-highest text-text-tertiary hover:text-text-secondary'}`}
                  >
                    {cap}
                  </button>
                )
              })}
            </div>
          ) : (
            <div className="flex flex-wrap gap-1 mt-1">
              {agent.capabilities.map((cap) => (
                <span key={cap} className="text-xs px-2 py-0.5 rounded-full bg-stitch-secondary-container text-stitch-on-secondary-container">{cap}</span>
              ))}
            </div>
          )}
        </div>
        <div>
          <label className={labelClass}>Heartbeat</label>
          {editing ? (
            <div className="space-y-2 mt-1">
              <label className="flex items-center gap-2 text-sm text-text-secondary">
                <input type="checkbox" checked={form.heartbeat_enabled} onChange={(e) => setForm((f) => ({ ...f, heartbeat_enabled: e.target.checked }))} className="rounded" />
                Enabled
              </label>
              {form.heartbeat_enabled && (
                <div className="flex items-center gap-2">
                  <input type="number" value={form.heartbeat_interval_seconds / 3600} onChange={(e) => setForm((f) => ({ ...f, heartbeat_interval_seconds: Number(e.target.value) * 3600 }))} min={1} className={`${inputClass} w-20`} />
                  <span className="text-xs text-text-tertiary">hours</span>
                </div>
              )}
            </div>
          ) : agent.heartbeat_enabled ? (
            <p className="text-sm">Every {(agent.heartbeat_interval_seconds || 0) / 3600}h</p>
          ) : (
            <p className="text-sm text-text-tertiary">Disabled</p>
          )}
        </div>
        <div>
          <label className={labelClass}>Job Description</label>
          {editing ? (
            <textarea value={form.job_description} onChange={(e) => setForm((f) => ({ ...f, job_description: e.target.value }))} rows={3} placeholder="Describe what this agent does..." className={inputClass} />
          ) : (
            <p className="text-sm text-text-secondary whitespace-pre-wrap">{agent.job_description || <span className="text-text-tertiary">—</span>}</p>
          )}
        </div>
        <div>
          <label className={labelClass}>Monthly Budget</label>
          {editing ? (
            <div className="flex items-center gap-2 mt-1">
              <span className="text-sm text-text-secondary">$</span>
              <input type="number" value={form.monthly_budget_cents != null ? form.monthly_budget_cents / 100 : ''} onChange={(e) => setForm((f) => ({ ...f, monthly_budget_cents: e.target.value ? Math.round(Number(e.target.value) * 100) : null }))} placeholder="No limit" min={0} step={0.01} className={`${inputClass} w-32`} />
            </div>
          ) : agent.monthly_budget_cents != null ? (
            <p className="text-sm">
              ${(agent.current_month_spent_cents / 100).toFixed(2)} / ${(agent.monthly_budget_cents / 100).toFixed(2)}
            </p>
          ) : (
            <p className="text-sm text-text-tertiary">No limit</p>
          )}
        </div>
        <div className="pt-4 border-t border-stitch-outline-variant/20">
          <button
            onClick={() => onDelete(agent.id)}
            className="w-full px-4 py-2 rounded-lg bg-red-500/10 text-red-400 text-sm hover:bg-red-500/20 transition-colors"
          >
            Remove Agent
          </button>
        </div>
      </div>
    </div>
  )
}

function TemplateSelector({ onApplied }: { onApplied: () => void }) {
  const [templates, setTemplates] = useState<OrgTemplate[]>([])
  const [applying, setApplying] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    agentTemplatesApi.list().then(setTemplates).catch((e) => setError(`Failed to load templates: ${e}`))
  }, [])

  const handleApply = async (templateId: string) => {
    setApplying(templateId)
    setError('')
    try {
      await agentTemplatesApi.apply(templateId)
      onApplied()
    } catch (e) {
      setError(`Failed to apply template: ${(e as Error).message}`)
    } finally {
      setApplying(null)
    }
  }

  function renderTree(agents: OrgTemplate['agents'][0][], indent: number = 0): React.ReactNode {
    return agents.map((a, i) => (
      <div key={i}>
        <div className="flex items-center gap-1" style={{ paddingLeft: indent * 16 }}>
          {indent > 0 && <span className="text-text-tertiary text-xs">└</span>}
          <span className="text-xs text-text-primary truncate">{a.name}</span>
        </div>
        {a.children && renderTree(a.children, indent + 1)}
      </div>
    ))
  }

  return (
    <div className="flex flex-col items-center py-8 flex-1 min-h-0 w-full">
      <span className="material-symbols-outlined text-4xl mb-3 text-text-tertiary">groups</span>
      <h3 className="text-lg font-bold text-text-primary mb-1">Build Your AI Team</h3>
      <p className="text-sm text-text-secondary mb-6">Choose a template to get started, or hire agents individually</p>
      {error && <p className="text-sm text-stitch-error mb-4">{error}</p>}

      {/*
        Scrollable grid: each card has fixed-ish height, the container caps at the
        remaining viewport with overflow-y so >3 templates do not get clipped at
        the bottom of the page.
      */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-3xl w-full overflow-y-auto px-1 pb-4">
        {templates.map((t) => (
          <div key={t.id} className="bg-stitch-surface-low rounded-xl p-5 border border-stitch-outline-variant/10 flex flex-col">
            <h4 className="text-sm font-bold text-text-primary mb-1">{t.name}</h4>
            <p className="text-xs text-text-tertiary mb-3">{t.description}</p>
            <div className="bg-stitch-surface-lowest rounded-lg p-3 mb-3 flex-1 space-y-0.5">
              {renderTree(t.agents)}
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-text-tertiary">{t.agent_count} agents</span>
              <button
                onClick={() => handleApply(t.id)}
                disabled={applying !== null}
                className="text-xs px-3 py-1.5 rounded-md bg-stitch-primary text-stitch-on-primary font-bold hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {applying === t.id ? 'Creating...' : 'Use Template'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function AgentsPage() {
  const { orgChart, fetchOrgChart, loading, selectedAgent, selectAgent, agents, fetchAgents, deleteAgent } = useAgentStore()
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [executorConfigs, setExecutorConfigs] = useState<ExecutorConfig[]>([])
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [resetting, setResetting] = useState(false)

  useEffect(() => {
    fetchOrgChart()
    fetchAgents()
    executorConfigsApi.list().then(setExecutorConfigs).catch(() => {})
  }, [fetchOrgChart, fetchAgents])

  const handleDelete = async (id: string) => {
    if (!confirm('Remove this agent?')) return
    await deleteAgent(id)
    selectAgent(null)
    await fetchOrgChart()
    await fetchAgents()
  }

  const handleReset = async () => {
    setResetting(true)
    try {
      // Delete all agents in parallel for speed (sequential was painful past
      // ~10 agents). Re-fetch and retry once to clean up rows whose parallel
      // delete raced against a parent's parent_agent_id SET NULL trigger and
      // returned a 4xx — the second pass always finishes with an empty list.
      await Promise.allSettled(agents.map((a) => deleteAgent(a.id)))
      await fetchAgents()
      const remaining = useAgentStore.getState().agents
      if (remaining.length > 0) {
        await Promise.allSettled(remaining.map((a) => deleteAgent(a.id)))
        await fetchAgents()
      }
      selectAgent(null)
      await fetchOrgChart()
    } finally {
      setResetting(false)
      setShowResetConfirm(false)
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      <Header
        title="Agent Organization"
        action={
          <div className="flex items-center gap-2">
            {agents.length > 0 && (
              <button
                onClick={() => setShowResetConfirm(true)}
                className="bg-stitch-surface-highest text-text-secondary px-3 py-1.5 rounded-md text-sm font-semibold hover:text-stitch-error transition-colors"
              >
                Reset
              </button>
            )}
            <button
              onClick={() => setShowCreateModal(true)}
              className="bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container px-4 py-1.5 rounded-md text-sm font-bold shadow-lg shadow-stitch-primary/20 hover:opacity-90 transition-opacity"
            >
              + Hire Agent
            </button>
          </div>
        }
      />
      <div className="flex-1 flex flex-col p-4 pb-0 overflow-hidden">
      <p className="text-xs text-text-secondary mb-2 px-2">
        {agents.length} agents · {agents.filter((a) => a.status === 'online').length} online
      </p>

      {/* Org Chart — fills remaining height */}
      {loading ? (
        <div className="flex items-center justify-center flex-1 text-text-secondary">Loading org chart...</div>
      ) : orgChart.length === 0 ? (
        <TemplateSelector onApplied={() => { fetchOrgChart(); fetchAgents() }} />
      ) : (
        <OrgChart orgChart={orgChart} />
      )}

      {/* Agent Detail Sidebar */}
      {selectedAgent && (
        <AgentDetailSidebar
          agent={selectedAgent}
          onClose={() => selectAgent(null)}
          onDelete={handleDelete}
          executorConfigs={executorConfigs}
        />
      )}

      {/* Create Modal */}
      {showCreateModal && <HireAgentModal onClose={() => setShowCreateModal(false)} agents={agents} executorConfigs={executorConfigs} />}

      {/* Reset confirmation */}
      {showResetConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowResetConfirm(false)}>
          <div className="bg-stitch-surface-low rounded-xl w-full max-w-sm p-6 border border-stitch-outline-variant/20" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-text-primary mb-2">Reset All Agents</h3>
            <p className="text-sm text-text-secondary mb-4">
              This will delete all {agents.length} agents. You can then select a new template to start over.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setShowResetConfirm(false)} className="flex-1 px-4 py-2 rounded-lg bg-stitch-surface-highest text-text-secondary text-sm">Cancel</button>
              <button onClick={handleReset} disabled={resetting} className="flex-1 px-4 py-2 rounded-lg bg-red-500/20 text-red-400 text-sm font-bold hover:bg-red-500/30 disabled:opacity-50">
                {resetting ? 'Deleting...' : 'Delete All'}
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  )
}
