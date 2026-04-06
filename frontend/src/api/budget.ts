import apiClient from './client'
import type { BudgetOverview, CostRecord } from '../types/budget'

export const budgetApi = {
  getSummary: () =>
    apiClient.get<BudgetOverview>('/api/v1/budget/summary').then((r) => r.data),

  getRecords: (params?: { agent_id?: string; limit?: number; offset?: number }) =>
    apiClient.get<CostRecord[]>('/api/v1/budget/records', { params }).then((r) => r.data),

  resetMonthly: () =>
    apiClient.post<{ reset_count: number }>('/api/v1/budget/reset').then((r) => r.data),
}
