import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

test.describe('Agents Page — Org Chart & Agent Management', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/agents')
  })

  test('displays "Agent Organization" heading', async ({ page }) => {
    await expect(page.getByText('Agent Organization')).toBeVisible()
  })

  test('shows agent count summary', async ({ page }) => {
    await expect(page.getByText(/3 agents/)).toBeVisible()
  })

  test('shows "Hire Agent" button', async ({ page }) => {
    await expect(page.getByRole('button', { name: /Hire Agent/ })).toBeVisible()
  })

  test('renders org chart with agent cards', async ({ page }) => {
    // Alex (CTO) should be visible
    await expect(page.getByText('Alex')).toBeVisible()
    await expect(page.getByText('cto')).toBeVisible()

    // Dev-1 (Engineer under CTO)
    await expect(page.getByText('Dev-1')).toBeVisible()

    // Writer-Bot (separate root)
    await expect(page.getByText('Writer-Bot')).toBeVisible()
  })

  test('shows executor type badges on agent cards', async ({ page }) => {
    await expect(page.getByText('BSGateway')).toBeVisible()
    await expect(page.getByText('Claude Code')).toBeVisible()
    await expect(page.getByText('Generic LLM')).toBeVisible()
  })

  test('shows status indicators on agent cards', async ({ page }) => {
    // online and busy statuses should be shown
    await expect(page.getByText('online').first()).toBeVisible()
    await expect(page.getByText('busy')).toBeVisible()
  })

  test('shows budget usage on agent cards', async ({ page }) => {
    // Alex: $12 / $60
    await expect(page.getByText('$12 / $60')).toBeVisible()
  })

  test('clicking agent card opens detail sidebar', async ({ page }) => {
    // Click the Alex card (first button with that text)
    await page.locator('button:has-text("Alex")').first().click()

    // Detail sidebar (fixed right panel) should appear with agent details
    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()
    await expect(sidebar.getByText('Alex').first()).toBeVisible()
    await expect(sidebar.getByText('cto').first()).toBeVisible()
  })

  test('detail sidebar shows capabilities', async ({ page }) => {
    await page.locator('button:has-text("Alex")').first().click()

    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar.getByText('coding')).toBeVisible()
    await expect(sidebar.getByText('analysis')).toBeVisible()
  })

  test('detail sidebar shows heartbeat info', async ({ page }) => {
    await page.locator('button:has-text("Alex")').first().click()

    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar.getByText(/4h/)).toBeVisible()
  })

  test('detail sidebar close button works', async ({ page }) => {
    await page.locator('button:has-text("Alex")').first().click()

    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()

    await sidebar.getByRole('button', { name: '✕' }).click()
    await expect(sidebar).not.toBeVisible()
  })

  test('detail sidebar shows edit button', async ({ page }) => {
    await page.locator('button:has-text("Alex")').first().click()

    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()
    const editBtn = sidebar.locator('button[title="Edit"]')
    await expect(editBtn).toBeVisible()
  })

  test('clicking edit shows Save and Cancel buttons', async ({ page }) => {
    await page.locator('button:has-text("Alex")').first().click()

    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()
    await sidebar.locator('button[title="Edit"]').click()

    await expect(sidebar.getByRole('button', { name: 'Save' })).toBeVisible()
    await expect(sidebar.getByRole('button', { name: 'Cancel' })).toBeVisible()
  })

  test('cancel edit returns to view mode', async ({ page }) => {
    await page.locator('button:has-text("Alex")').first().click()

    const sidebar = page.getByTestId('agent-detail-sidebar')
    await expect(sidebar).toBeVisible()
    await sidebar.locator('button[title="Edit"]').click()
    await expect(sidebar.getByRole('button', { name: 'Save' })).toBeVisible()

    await sidebar.getByRole('button', { name: 'Cancel' }).click()
    await expect(sidebar.getByRole('button', { name: 'Save' })).not.toBeVisible()
    await expect(sidebar.locator('button[title="Edit"]')).toBeVisible()
  })
})

test.describe('Agents Page — Navigation', () => {
  test('sidebar Agents link navigates to /agents', async ({ page }) => {
    await setupPage(page, '/dashboard')
    const sidebar = page.locator('aside').first()
    await sidebar.getByRole('link', { name: 'Agents' }).click()
    await expect(page).toHaveURL('/agents')
  })

  test('sidebar shows Agents nav item with groups icon', async ({ page }) => {
    await setupPage(page, '/dashboard')
    const sidebar = page.locator('aside').first()
    const agentsLink = sidebar.getByRole('link', { name: 'Agents' })
    await expect(agentsLink).toBeVisible()
    await expect(agentsLink.locator('span.material-symbols-outlined:has-text("groups")')).toBeVisible()
  })

  test('Agents link is active on /agents page', async ({ page }) => {
    await setupPage(page, '/agents')
    const sidebar = page.locator('aside').first()
    const agentsLink = sidebar.getByRole('link', { name: 'Agents' })
    await expect(agentsLink).toHaveClass(/font-semibold/)
  })
})
