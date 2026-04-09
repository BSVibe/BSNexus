import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Header from '../components/layout/Header'
import { StatCard, Button, Modal } from '../components/common'
import { budgetApi } from '../api/budget'
import { agentsApi } from '../api/agents'
import type { AgentBudgetSummary, CostRecord } from '../types/budget'

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

function UtilizationBar({ pct }: { pct: number }) {
  const color =
    pct >= 90 ? 'bg-stitch-error' : pct >= 70 ? 'bg-yellow-500' : 'bg-stitch-primary'
  return (
    <div className="h-1.5 w-full bg-stitch-surface-highest rounded-full overflow-hidden">
      <div
        className={`h-full rounded-full ${color}`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  )
}

function AgentBudgetCard({ summary, onEdit }: { summary: AgentBudgetSummary; onEdit: (s: AgentBudgetSummary) => void }) {
  const pct = summary.utilization_pct ?? 0
  return (
    <button
      type="button"
      onClick={() => onEdit(summary)}
      className="bg-stitch-surface-low rounded-xl p-5 border border-stitch-outline-variant/10 text-left w-full hover:border-stitch-primary/30 transition-colors cursor-pointer"
    >
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-bold text-text-primary">{summary.agent_name}</h4>
        {summary.utilization_pct !== null && (
          <span
            className={`text-xs font-bold ${pct >= 90 ? 'text-stitch-error' : pct >= 70 ? 'text-yellow-500' : 'text-stitch-primary'}`}
          >
            {pct.toFixed(0)}%
          </span>
        )}
      </div>
      {summary.monthly_budget_cents !== null ? (
        <>
          <UtilizationBar pct={pct} />
          <div className="flex justify-between mt-2 text-xs text-text-tertiary">
            <span>{formatCents(summary.current_month_spent_cents)} spent</span>
            <span>{formatCents(summary.monthly_budget_cents)} budget</span>
          </div>
        </>
      ) : (
        <p className="text-xs text-text-tertiary">No budget limit set</p>
      )}
    </button>
  )
}

function CostRecordsTable({
  records,
  agentNames,
}: {
  records: CostRecord[]
  agentNames: Record<string, string>
}) {
  if (records.length === 0) {
    return <p className="text-sm text-text-tertiary py-4">No cost records yet.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-text-secondary border-b border-stitch-outline-variant/20">
            <th className="pb-2 pr-4">Date</th>
            <th className="pb-2 pr-4">Agent</th>
            <th className="pb-2 pr-4">Model</th>
            <th className="pb-2 pr-4 text-right">Tokens</th>
            <th className="pb-2 text-right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r) => (
            <tr
              key={r.id}
              className="border-b border-stitch-outline-variant/10 text-text-primary"
            >
              <td className="py-2 pr-4 text-text-tertiary">
                {new Date(r.recorded_at).toLocaleDateString()}
              </td>
              <td className="py-2 pr-4">{agentNames[r.agent_id] ?? r.agent_id.slice(0, 8)}</td>
              <td className="py-2 pr-4 text-text-secondary">{r.model_name ?? '-'}</td>
              <td className="py-2 pr-4 text-right text-text-secondary">
                {r.token_count?.toLocaleString() ?? '-'}
              </td>
              <td className="py-2 text-right font-medium">{formatCents(r.amount_cents)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function BudgetPage() {
  const queryClient = useQueryClient()
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<AgentBudgetSummary | null>(null)
  const [editBudgetDollars, setEditBudgetDollars] = useState('')

  const budgetUpdateMutation = useMutation({
    mutationFn: async ({ agentId, budgetCents }: { agentId: string; budgetCents: number | null }) => {
      return agentsApi.update(agentId, { monthly_budget_cents: budgetCents })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budget'] })
      setEditTarget(null)
    },
  })

  const openBudgetEdit = (summary: AgentBudgetSummary) => {
    setEditTarget(summary)
    setEditBudgetDollars(summary.monthly_budget_cents != null ? (summary.monthly_budget_cents / 100).toFixed(2) : '')
  }

  const { data: overview, isLoading } = useQuery({
    queryKey: ['budget', 'summary'],
    queryFn: budgetApi.getSummary,
  })

  const { data: records = [] } = useQuery({
    queryKey: ['budget', 'records'],
    queryFn: () => budgetApi.getRecords({ limit: 50 }),
  })

  const resetMutation = useMutation({
    mutationFn: budgetApi.resetMonthly,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budget'] })
      setResetConfirmOpen(false)
    },
  })

  const agentNames: Record<string, string> = {}
  overview?.agent_summaries.forEach((s) => {
    agentNames[s.agent_id] = s.agent_name
  })

  const totalUtilization =
    overview && overview.total_budget_cents > 0
      ? ((overview.total_spent_cents / overview.total_budget_cents) * 100).toFixed(0)
      : '—'

  return (
    <>
      <Header
        title="Budget"
        action={
          <Button variant="secondary" onClick={() => setResetConfirmOpen(true)}>
            Reset Monthly
          </Button>
        }
      />
      <div className="p-8 space-y-8">
        {isLoading ? (
          <p className="text-sm text-text-tertiary">Loading budget data...</p>
        ) : overview ? (
          <>
            {/* Stat cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <StatCard
                label="Total Budget"
                value={formatCents(overview.total_budget_cents)}
                icon="account_balance_wallet"
              />
              <StatCard
                label="Total Spent"
                value={formatCents(overview.total_spent_cents)}
                icon="payments"
              />
              <StatCard
                label="Remaining"
                value={formatCents(overview.total_remaining_cents)}
                icon="savings"
              />
              <StatCard
                label="Utilization"
                value={`${totalUtilization}%`}
                icon="speed"
              />
            </div>

            {/* Agent budget cards */}
            {overview.agent_summaries.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider mb-4">
                  Agent Budgets
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {overview.agent_summaries.map((s) => (
                    <AgentBudgetCard key={s.agent_id} summary={s} onEdit={openBudgetEdit} />
                  ))}
                </div>
              </div>
            )}

            {/* Cost records */}
            <div>
              <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider mb-4">
                Recent Cost Records
              </h3>
              <div className="bg-stitch-surface-low rounded-xl p-5 border border-stitch-outline-variant/10">
                <CostRecordsTable records={records} agentNames={agentNames} />
              </div>
            </div>
          </>
        ) : (
          <p className="text-sm text-stitch-error">Failed to load budget data.</p>
        )}
      </div>

      {/* Reset confirmation modal */}
      <Modal
        open={resetConfirmOpen}
        onClose={() => setResetConfirmOpen(false)}
        title="Reset Monthly Budgets"
        footer={
          <>
            <Button variant="secondary" onClick={() => setResetConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => resetMutation.mutate()}
              loading={resetMutation.isPending}
            >
              Reset
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-secondary">
          This will reset all agents' monthly spend counters to zero. This action cannot be undone.
        </p>
      </Modal>

      {/* Budget edit modal */}
      <Modal
        open={editTarget !== null}
        onClose={() => setEditTarget(null)}
        title={`Edit Budget — ${editTarget?.agent_name}`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (!editTarget) return
                const cents = editBudgetDollars ? Math.round(Number(editBudgetDollars) * 100) : null
                budgetUpdateMutation.mutate({ agentId: editTarget.agent_id, budgetCents: cents })
              }}
              loading={budgetUpdateMutation.isPending}
            >
              Save
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-sm text-text-secondary mb-1.5">Monthly Budget ($)</label>
            <input
              type="number"
              value={editBudgetDollars}
              onChange={(e) => setEditBudgetDollars(e.target.value)}
              placeholder="No limit"
              min={0}
              step={0.01}
              className="w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary"
            />
            <p className="mt-1 text-xs text-text-tertiary">
              Leave empty to remove budget limit.
            </p>
          </div>
          {editTarget && editTarget.current_month_spent_cents > 0 && (
            <p className="text-xs text-text-tertiary">
              Current month spent: {formatCents(editTarget.current_month_spent_cents)}
            </p>
          )}
        </div>
      </Modal>
    </>
  )
}
