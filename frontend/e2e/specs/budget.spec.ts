import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

test.describe('Budget Page — Overview & Cost Records', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/budget')
  })

  test('header displays "Budget" title', async ({ page }) => {
    await expect(page.locator('header').getByText('Budget')).toBeVisible()
  })

  test('header has "Reset Monthly" button', async ({ page }) => {
    await expect(page.getByRole('button', { name: /Reset Monthly/ })).toBeVisible()
  })

  test('renders four stat cards', async ({ page }) => {
    await expect(page.getByText('Total Budget')).toBeVisible()
    await expect(page.getByText('Total Spent')).toBeVisible()
    await expect(page.getByText('Remaining')).toBeVisible()
    await expect(page.getByText('Utilization')).toBeVisible()
  })

  test('stat cards show correct budget values', async ({ page }) => {
    // $460.00 total budget (46000 cents)
    await expect(page.getByText('$460.00')).toBeVisible()
    // $59.00 total spent (5900 cents)
    await expect(page.getByText('$59.00')).toBeVisible()
  })

  test('shows Agent Budgets section', async ({ page }) => {
    await expect(page.getByText('Agent Budgets')).toBeVisible()
  })

  test('renders agent budget cards with names', async ({ page }) => {
    await expect(page.getByText('Alex').first()).toBeVisible()
    await expect(page.getByText('Dev-1').first()).toBeVisible()
    await expect(page.getByText('Writer-Bot').first()).toBeVisible()
  })

  test('agent budget cards show utilization percentage', async ({ page }) => {
    await expect(page.getByText('20%').first()).toBeVisible()
    await expect(page.getByText('15%').first()).toBeVisible()
  })

  test('shows Recent Cost Records section', async ({ page }) => {
    await expect(page.getByText('Recent Cost Records')).toBeVisible()
  })

  test('cost records table shows model names', async ({ page }) => {
    await expect(page.getByText('claude-3.5-sonnet')).toBeVisible()
    await expect(page.getByText('gpt-4o')).toBeVisible()
  })

  test('cost records table shows cost amounts', async ({ page }) => {
    await expect(page.getByText('$5.00')).toBeVisible()
    // 200 cents = $2.00 — use table row scope to avoid matching other $2.00 on page
    const table = page.locator('table')
    await expect(table.getByText('$2.00')).toBeVisible()
  })

  test('Budget nav item is active in sidebar', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const budgetLink = sidebar.getByRole('link', { name: 'Budget' })
    await expect(budgetLink).toHaveClass(/font-semibold/)
  })
})

test.describe('Budget Page — Reset Modal', () => {
  test('clicking Reset Monthly opens confirmation modal', async ({ page }) => {
    await setupPage(page, '/budget')
    await page.getByRole('button', { name: /Reset Monthly/ }).click()
    await expect(page.getByText('Reset Monthly Budgets')).toBeVisible()
    await expect(page.getByText('cannot be undone')).toBeVisible()
  })

  test('cancel closes the reset modal', async ({ page }) => {
    await setupPage(page, '/budget')
    await page.getByRole('button', { name: /Reset Monthly/ }).click()
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByText('cannot be undone')).not.toBeVisible()
  })
})
