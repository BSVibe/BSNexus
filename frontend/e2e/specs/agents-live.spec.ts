/**
 * Live API E2E tests for Agent management.
 * These tests hit the real backend API (not mocked).
 * Requires: backend running at localhost:8000, frontend at localhost:3000
 */
import { test, expect } from '@playwright/test'
import { injectAuth } from '../helpers/mock-api'

const API = 'http://localhost:8000'

test.describe('Agents — Live API E2E', () => {
  test.beforeEach(async ({ page }) => {
    // Auth is still mocked (SSO requires real auth server)
    // but all /api/v1/agents calls hit the real backend
    await injectAuth(page)

    // Mock auth + non-agent endpoints that the app loads globally
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'user-001', email: 'dev@bsvibe.dev' }),
      }),
    )
    await page.route('**/api/v1/dashboard/**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
    )
    await page.route('**/api/v1/settings', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ llm_api_key: null, llm_model: null, llm_base_url: null }),
      }),
    )
    await page.route('**/api/v1/projects', (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return route.continue()
    })
  })

  test('agents page loads and shows seed agents from real API', async ({ page }) => {
    await page.goto('/agents', { waitUntil: 'networkidle' })

    await expect(page.getByText('Agent Organization')).toBeVisible()
    // Company OS seed agents
    await expect(page.locator('button:has-text("CEO")').first()).toBeVisible()
    await expect(page.locator('button:has-text("CTO")').first()).toBeVisible()
  })

  test('Hire Agent modal opens and creates agent via real API', async ({ page }) => {
    await page.goto('/agents', { waitUntil: 'networkidle' })
    // Wait for agents to render
    await expect(page.getByText('Agent Organization')).toBeVisible()

    // Open modal
    const hireBtn = page.locator('button:has-text("Hire Agent")').first()
    await hireBtn.scrollIntoViewIfNeeded()
    await hireBtn.click()
    await expect(page.getByText('Hire New Agent')).toBeVisible()

    // Fill form
    await page.getByPlaceholder('e.g. Alex').fill('E2E Test Bot')
    await page.getByPlaceholder('e.g. engineer').fill('tester')
    await page.getByPlaceholder('e.g. Senior').fill('E2E Tester')

    // Submit (exact match to avoid matching the header "+ Hire Agent" button)
    await page.getByRole('button', { name: 'Hire Agent', exact: true }).click()

    // Modal should close and new agent should appear
    await expect(page.getByText('Hire New Agent')).not.toBeVisible()
    await expect(page.getByText('E2E Test Bot').first()).toBeVisible({ timeout: 5000 })
  })

  test('clicking agent card shows detail sidebar with real data', async ({ page }) => {
    await page.goto('/agents', { waitUntil: 'networkidle' })

    // Click the CTO card
    await page.locator('button:has-text("CTO")').first().click()

    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()
    await expect(sidebar.getByText('CTO').first()).toBeVisible()
    // Close sidebar
    await sidebar.getByRole('button', { name: '✕' }).click()
    await expect(sidebar).not.toBeVisible()
  })

  test('remove agent works via real API', async ({ page }) => {
    // First create an agent to delete via API
    const createRes = await page.request.post(`${API}/api/v1/agents`, {
      data: { name: 'Deletable Bot', role: 'temp' },
    })
    expect(createRes.status()).toBe(201)
    const created = await createRes.json()

    await page.goto('/agents', { waitUntil: 'networkidle' })
    const card = page.locator('button:has-text("Deletable Bot")').first()
    await card.scrollIntoViewIfNeeded()
    await expect(card).toBeVisible()

    // Click to open sidebar
    await card.click()
    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()

    // Accept confirm dialog before clicking remove
    page.on('dialog', (dialog) => dialog.accept())

    // Click remove
    await sidebar.getByRole('button', { name: 'Remove Agent' }).click()

    // Agent should disappear from the page
    await expect(page.locator('button:has-text("Deletable Bot")')).not.toBeVisible({ timeout: 5000 })

    // Verify via API that it's gone
    const getRes = await page.request.get(`${API}/api/v1/agents/${created.id}`)
    expect(getRes.status()).toBe(404)
  })

  test('agents API returns correct structure', async ({ page }) => {
    const res = await page.request.get(`${API}/api/v1/agents`)
    expect(res.status()).toBe(200)
    const agents = await res.json()
    expect(Array.isArray(agents)).toBe(true)
    expect(agents.length).toBeGreaterThan(0)

    const agent = agents[0]
    expect(agent).toHaveProperty('id')
    expect(agent).toHaveProperty('name')
    expect(agent).toHaveProperty('role')
    expect(agent).toHaveProperty('executor_type')
    expect(agent).toHaveProperty('capabilities')
    expect(agent).toHaveProperty('status')
    expect(agent).toHaveProperty('monthly_budget_cents')
  })

  test('org-chart API returns tree structure', async ({ page }) => {
    const res = await page.request.get(`${API}/api/v1/agents/org-chart`)
    expect(res.status()).toBe(200)
    const tree = await res.json()
    expect(Array.isArray(tree)).toBe(true)

    // Each node has agent + children
    for (const node of tree) {
      expect(node).toHaveProperty('agent')
      expect(node).toHaveProperty('children')
      expect(node.agent).toHaveProperty('name')
    }
  })

  test('goals API returns mission goal', async ({ page }) => {
    const res = await page.request.get(`${API}/api/v1/goals`)
    expect(res.status()).toBe(200)
    const goals = await res.json()
    expect(goals.length).toBeGreaterThan(0)
    expect(goals.some((g: { level: string }) => g.level === 'mission')).toBe(true)
  })
})
