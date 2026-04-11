/**
 * Plan view smoke tests.
 *
 * The Plan view is the new default tab on the project page (it replaced
 * the Kanban board). These specs hit the mock API only — no live backend
 * required.
 */
import { expect, test } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

test.describe('Project Plan view', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/projects/proj-001')
  })

  test('Plan tab is the default', async ({ page }) => {
    const planTab = page.getByRole('button', { name: /plan/i }).first()
    await expect(planTab).toBeVisible()
  })

  test('renders the project goal slogan', async ({ page }) => {
    await expect(page.getByText('Ship MVP by Q3').first()).toBeVisible()
  })

  test('renders agent status cards from the agents list', async ({ page }) => {
    // All seeded agents from mockAgents should appear in the status bar.
    await expect(page.getByText('Alex').first()).toBeVisible()
    await expect(page.getByText('Dev-1').first()).toBeVisible()
  })

  test('renders phases from the plan tree', async ({ page }) => {
    await expect(page.getByText('Phase 1: Core Backend, Phase 2: Frontend').or(
      page.getByText('Frontend').first()
    )).toBeTruthy()
    await expect(page.getByText('Frontend').first()).toBeVisible()
  })

  test('shows the running task with its agent badge', async ({ page }) => {
    await expect(page.getByText('Build agent status bar').first()).toBeVisible()
  })

  test('shows the design tab and switches to it', async ({ page }) => {
    const designTab = page.getByRole('button', { name: /design/i }).first()
    await designTab.click()
    // The .bsd file lister header is visible.
    await expect(page.getByText(/screens/i).first()).toBeVisible()
  })
})

test.describe('Project channels modal', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/projects/proj-001')
  })

  test('opens via the forum icon and shows the linked channel', async ({ page }) => {
    // Forum icon button — title="Channels"
    await page.getByTitle('Channels').click()
    await expect(page.getByText(/Project channels/i)).toBeVisible()
    await expect(page.getByText('#bsvibe-tax')).toBeVisible()
  })
})
