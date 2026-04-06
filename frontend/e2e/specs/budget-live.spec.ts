/**
 * Live E2E: Budget page — hits real /api/v1/budget/* endpoints.
 * Requires: backend running with seed data.
 */
import { test, expect } from '@playwright/test'
import { setupLivePage, API } from '../helpers/live-setup'

test.describe('Budget — Live API E2E', () => {
  test('budget summary API returns valid structure', async ({ page }) => {
    const res = await page.request.get(`${API}/api/v1/budget/summary`)
    expect(res.status()).toBe(200)
    const data = await res.json()
    expect(data).toHaveProperty('total_budget_cents')
    expect(data).toHaveProperty('total_spent_cents')
    expect(data).toHaveProperty('total_remaining_cents')
    expect(data).toHaveProperty('agent_summaries')
    expect(Array.isArray(data.agent_summaries)).toBe(true)
  })

  test('budget records API returns list', async ({ page }) => {
    const res = await page.request.get(`${API}/api/v1/budget/records`)
    expect(res.status()).toBe(200)
    const records = await res.json()
    expect(Array.isArray(records)).toBe(true)
  })

  test('budget page renders stat cards from real API', async ({ page }) => {
    await setupLivePage(page, '/budget')

    await expect(page.getByText('Total Budget')).toBeVisible()
    await expect(page.getByText('Total Spent')).toBeVisible()
    await expect(page.getByText('Remaining')).toBeVisible()
    await expect(page.getByText('Utilization')).toBeVisible()
  })

  test('budget page shows Agent Budgets section with seed agents', async ({ page }) => {
    await setupLivePage(page, '/budget')

    await expect(page.getByText('Agent Budgets')).toBeVisible()
    // Seed agents should appear (Architect, Developer, Reviewer from seed_data.py)
    await expect(page.getByText('Architect').first()).toBeVisible()
  })

  test('budget page shows Recent Cost Records section', async ({ page }) => {
    await setupLivePage(page, '/budget')

    await expect(page.getByText('Recent Cost Records')).toBeVisible()
  })

  test('Budget nav item is active in sidebar', async ({ page }) => {
    await setupLivePage(page, '/budget')

    const sidebar = page.locator('aside').first()
    const budgetLink = sidebar.getByRole('link', { name: 'Budget' })
    await expect(budgetLink).toHaveClass(/font-semibold/)
  })

  test('Reset Monthly button opens confirmation modal', async ({ page }) => {
    await setupLivePage(page, '/budget')

    await page.getByRole('button', { name: /Reset Monthly/ }).click()
    await expect(page.getByText('Reset Monthly Budgets')).toBeVisible()
    await expect(page.getByText('cannot be undone')).toBeVisible()

    // Cancel should close modal
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByText('cannot be undone')).not.toBeVisible()
  })

  test('reset monthly budget via real API', async ({ page }) => {
    await setupLivePage(page, '/budget')

    // Get current summary
    const before = await page.request.get(`${API}/api/v1/budget/summary`)
    const beforeData = await before.json()

    // Reset
    const resetRes = await page.request.post(`${API}/api/v1/budget/reset`)
    expect(resetRes.status()).toBe(200)
    const resetData = await resetRes.json()
    expect(resetData).toHaveProperty('reset_count')
    expect(resetData.reset_count).toBeGreaterThanOrEqual(0)

    // Verify spent is 0 after reset
    const after = await page.request.get(`${API}/api/v1/budget/summary`)
    const afterData = await after.json()
    expect(afterData.total_spent_cents).toBe(0)
  })
})
