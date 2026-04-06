/**
 * Live E2E: Settings page.
 * Settings API requires admin_settings permission (auth guard).
 * We mock settings API responses but verify UI rendering and interactions.
 */
import { test, expect } from '@playwright/test'
import { setupLivePage } from '../helpers/live-setup'

test.describe('Settings — Live API E2E', () => {
  test('settings page loads and shows LLM Configuration', async ({ page }) => {
    await setupLivePage(page, '/settings')

    await expect(page.getByText('LLM Configuration')).toBeVisible()
    await expect(page.getByText('API Key')).toBeVisible()
    await expect(page.getByText('Model')).toBeVisible()
    await expect(page.getByText('Base URL (Optional)')).toBeVisible()
    await expect(page.getByText('Default Executor')).toBeVisible()
  })

  test('executor dropdown is populated with 5 options', async ({ page }) => {
    await setupLivePage(page, '/settings')

    const select = page.locator('select')
    await expect(select).toBeVisible()

    const options = select.locator('option')
    await expect(options).toHaveCount(5)
  })

  test('changing executor updates description text', async ({ page }) => {
    await setupLivePage(page, '/settings')

    const select = page.locator('select')
    await select.selectOption('bsgateway')
    await expect(page.getByText('BSGateway proxy with cost tracking')).toBeVisible()

    await select.selectOption('generic_llm')
    await expect(page.getByText('Any LiteLLM-compatible model')).toBeVisible()

    await select.selectOption('claude_code')
    await expect(page.getByText('Claude Code CLI for code generation tasks')).toBeVisible()
  })

  test('Save Settings button is visible', async ({ page }) => {
    await setupLivePage(page, '/settings')
    await expect(page.getByRole('button', { name: /Save Settings/ })).toBeVisible()
  })

  test('Settings nav item is active in sidebar', async ({ page }) => {
    await setupLivePage(page, '/settings')

    const sidebar = page.locator('aside').first()
    await expect(sidebar.getByRole('link', { name: 'Settings' })).toHaveClass(/font-semibold/)
  })
})
