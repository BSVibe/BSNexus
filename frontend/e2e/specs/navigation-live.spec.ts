/**
 * Live E2E: Navigation — sidebar items, routing, active states.
 * Hits real backend. Verifies the actual sidebar matches code changes.
 */
import { test, expect } from '@playwright/test'
import { setupLivePage } from '../helpers/live-setup'

test.describe('Navigation — Live E2E', () => {
  test('sidebar shows Dashboard, Agents, Budget, Settings nav items', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    const sidebar = page.locator('aside').first()
    await expect(sidebar.getByRole('link', { name: 'Dashboard' })).toBeVisible()
    await expect(sidebar.getByRole('link', { name: 'Agents' })).toBeVisible()
    await expect(sidebar.getByRole('link', { name: 'Budget' })).toBeVisible()
    await expect(sidebar.getByRole('link', { name: 'Settings' })).toBeVisible()
  })

  test('sidebar does NOT show Projects, Architect, or Settings button', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    const sidebar = page.locator('aside').first()
    // Old nav items should NOT exist
    await expect(sidebar.getByRole('link', { name: 'Projects' })).not.toBeVisible()
    await expect(sidebar.getByRole('link', { name: 'Architect' })).not.toBeVisible()
    // Settings is now a link, not a button
    await expect(sidebar.getByRole('button', { name: 'Settings' })).not.toBeVisible()
  })

  test('Dashboard is active on /dashboard', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    const sidebar = page.locator('aside').first()
    await expect(sidebar.getByRole('link', { name: 'Dashboard' })).toHaveClass(/font-semibold/)
  })

  test('navigate to /budget via sidebar', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    await page.locator('aside').first().getByRole('link', { name: 'Budget' }).click()
    await expect(page).toHaveURL('/budget')
    await expect(page.locator('header').getByRole('heading', { name: 'Budget' })).toBeVisible()
  })

  test('navigate to /settings via sidebar', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    await page.locator('aside').first().getByRole('link', { name: 'Settings' }).click()
    await expect(page).toHaveURL('/settings')
    await expect(page.locator('header').getByRole('heading', { name: 'Settings' })).toBeVisible()
  })

  test('navigate to /agents via sidebar', async ({ page }) => {
    await setupLivePage(page, '/dashboard')

    await page.locator('aside').first().getByRole('link', { name: 'Agents' }).click()
    await expect(page).toHaveURL('/agents')
    await expect(page.getByText('Agent Organization')).toBeVisible()
  })

  test('/budget shows Budget page with real API data', async ({ page }) => {
    await setupLivePage(page, '/budget')

    await expect(page.getByText('Total Budget')).toBeVisible()
    await expect(page.getByText('Total Spent')).toBeVisible()
    await expect(page.getByText('Remaining')).toBeVisible()
    await expect(page.getByText('Utilization')).toBeVisible()
  })

  test('/settings shows Settings page with real API data', async ({ page }) => {
    await setupLivePage(page, '/settings')

    await expect(page.getByText('LLM Configuration')).toBeVisible()
    await expect(page.getByText('Default Executor')).toBeVisible()
    const select = page.locator('select')
    await expect(select).toBeVisible()
  })
})
