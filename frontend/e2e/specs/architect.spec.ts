import { test, expect } from '@playwright/test'
import { setupMocks, MOCK_SESSIONS } from '../helpers/mock-api'

test.describe('Architect Page', () => {
  test.beforeEach(async ({ page }) => {
    await setupMocks(page)
  })

  test('shows empty state when no session selected', async ({ page }) => {
    await page.goto('/architect')

    await expect(page.getByText('BSNexus Architect')).toBeVisible()
    await expect(page.getByText(/Select a session from the sidebar/)).toBeVisible()
  })

  test('displays session list in sidebar', async ({ page }) => {
    await page.goto('/architect')

    await expect(page.getByText('Design Session Alpha')).toBeVisible()
    await expect(page.getByText('New Design Session')).toBeVisible()
  })

  test('loads session with messages when clicked', async ({ page }) => {
    await page.goto('/architect/session-1')

    // Should show the session messages
    await expect(page.getByText('Design a task management system', { exact: true })).toBeVisible()
    await expect(page.getByText(/I will design a task management system/)).toBeVisible()
  })

  test('shows connected status for active session', async ({ page }) => {
    await page.goto('/architect/session-1')

    await expect(page.getByText('Connected')).toBeVisible()
  })

  test('shows chat input for active session', async ({ page }) => {
    await page.goto('/architect/session-2')

    // session-2 is active (not project_bound), should have input
    const input = page.locator('textarea')
    await expect(input).toBeVisible()
  })

  test('shows AI design features text', async ({ page }) => {
    await page.goto('/architect')

    await expect(page.getByText('AI-powered design')).toBeVisible()
    await expect(page.getByText('Auto-decomposition')).toBeVisible()
  })

  test('shows empty message state for session with no messages', async ({ page }) => {
    await page.goto('/architect/session-2')

    await expect(page.getByText(/Describe the project/)).toBeVisible()
  })
})
