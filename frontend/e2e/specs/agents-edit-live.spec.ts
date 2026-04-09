/**
 * Live E2E: Agent edit sidebar — inline editing via real API.
 * Each test creates its own agent via API to avoid seed data dependency.
 */
import { test, expect } from '@playwright/test'
import { setupLivePage, API } from '../helpers/live-setup'

test.describe('Agent Edit — Live API E2E', () => {
  let testAgentId: string

  test.beforeEach(async ({ page }) => {
    // Create a dedicated test agent via API (no auth required for agents endpoint)
    const res = await page.request.post(`${API}/api/v1/agents`, {
      data: {
        name: `EditTest-${Date.now()}`,
        role: 'tester',
        title: 'QA Engineer',
        heartbeat_enabled: true,
        heartbeat_interval_seconds: 3600,
        monthly_budget_cents: 5000,
      },
    })
    expect(res.status()).toBe(201)
    const agent = await res.json()
    testAgentId = agent.id
  })

  test.afterEach(async ({ page }) => {
    // Cleanup test agent
    if (testAgentId) {
      await page.request.delete(`${API}/api/v1/agents/${testAgentId}`)
    }
  })

  test('detail sidebar shows edit button', async ({ page }) => {
    await setupLivePage(page, '/agents')

    // Find our test agent by partial name match
    const card = page.locator('button:has-text("EditTest")').first()
    await card.scrollIntoViewIfNeeded()
    await card.click()

    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()
    await expect(sidebar.locator('button[title="Edit"]')).toBeVisible()
  })

  test('edit mode shows input fields and Save/Cancel', async ({ page }) => {
    await setupLivePage(page, '/agents')

    await page.locator('button:has-text("EditTest")').first().click()
    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()

    await sidebar.locator('button[title="Edit"]').click()
    await expect(sidebar.getByRole('button', { name: 'Save' })).toBeVisible()
    await expect(sidebar.getByRole('button', { name: 'Cancel' })).toBeVisible()

    // Input fields should be visible (name, role, title, executor, description, budget, heartbeat)
    const inputs = sidebar.locator('input, textarea, select')
    const count = await inputs.count()
    expect(count).toBeGreaterThanOrEqual(5)
  })

  test('cancel edit returns to view mode without saving', async ({ page }) => {
    await setupLivePage(page, '/agents')

    await page.locator('button:has-text("EditTest")').first().click()
    const sidebar = page.getByTestId('agent-detail-sidebar')

    await sidebar.locator('button[title="Edit"]').click()
    const nameInput = sidebar.locator('input').first()
    await nameInput.fill('TEMP NAME SHOULD NOT SAVE')

    await sidebar.getByRole('button', { name: 'Cancel' }).click()

    // Verify original name still in sidebar (view mode)
    await expect(sidebar.locator('button[title="Edit"]')).toBeVisible()

    // Verify API still has original name
    const res = await page.request.get(`${API}/api/v1/agents/${testAgentId}`)
    const agent = await res.json()
    expect(agent.name).toContain('EditTest')
  })

  test('save edit persists changes via real API', async ({ page }) => {
    await setupLivePage(page, '/agents')

    await page.locator('button:has-text("EditTest")').first().click()
    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()

    // Edit title (3rd input: name, role, title)
    await sidebar.locator('button[title="Edit"]').click()
    const titleInput = sidebar.locator('input').nth(2)
    await titleInput.fill('Senior QA Engineer')

    await sidebar.getByRole('button', { name: 'Save' }).click()
    await expect(sidebar.locator('button[title="Edit"]')).toBeVisible({ timeout: 5000 })

    // Verify via API
    const res = await page.request.get(`${API}/api/v1/agents/${testAgentId}`)
    const updated = await res.json()
    expect(updated.title).toBe('Senior QA Engineer')
  })

  test('heartbeat checkbox is visible in edit mode', async ({ page }) => {
    await setupLivePage(page, '/agents')

    await page.locator('button:has-text("EditTest")').first().click()
    const sidebar = page.getByTestId('agent-detail-sidebar')

    await sidebar.locator('button[title="Edit"]').click()

    const heartbeatCheckbox = sidebar.locator('input[type="checkbox"]')
    await expect(heartbeatCheckbox).toBeVisible()

    await sidebar.getByRole('button', { name: 'Cancel' }).click()
  })
})
