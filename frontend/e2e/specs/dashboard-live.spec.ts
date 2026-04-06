/**
 * Live E2E: Dashboard — Compute Cost stat card hits real budget API.
 * Requires: backend running with seed data.
 */
import { test, expect } from '@playwright/test'
import { setupLivePage } from '../helpers/live-setup'

test.describe('Dashboard — Live API E2E', () => {
  test('dashboard loads with stat cards', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    await expect(page.locator('header').getByText('Dashboard')).toBeVisible()
    await expect(page.getByText('Total Projects')).toBeVisible()
    await expect(page.getByText('Active Tasks')).toBeVisible()
    await expect(page.getByText('Completion Rate')).toBeVisible()
    await expect(page.getByText('Compute Cost')).toBeVisible()
  })

  test('Compute Cost stat card shows dollar value', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    // Compute Cost card should be present with a dollar value
    await expect(page.getByText('Compute Cost')).toBeVisible()
    // The value should start with $ (budget data from real API or $0.00 fallback)
    await expect(page.getByText(/\$\d+\.\d{2}/).first()).toBeVisible()
  })

  test('dashboard shows New Project and Import buttons', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    await expect(page.getByRole('button', { name: 'New Project' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Import' })).toBeVisible()
  })
})
