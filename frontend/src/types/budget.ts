export interface AgentBudgetSummary {
  agent_id: string
  agent_name: string
  monthly_budget_cents: number | null
  current_month_spent_cents: number
  budget_remaining_cents: number | null
  utilization_pct: number | null
}

export interface CostRecord {
  id: string
  tenant_id: string
  agent_id: string
  task_id: string | null
  amount_cents: number
  token_count: number | null
  model_name: string | null
  recorded_at: string
}

export interface BudgetOverview {
  total_budget_cents: number
  total_spent_cents: number
  total_remaining_cents: number
  agent_summaries: AgentBudgetSummary[]
}
