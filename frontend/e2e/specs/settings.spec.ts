import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

test.describe('Settings Page — LLM Configuration', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/settings')
  })

  test('header displays "Settings" title', async ({ page }) => {
    await expect(page.locator('header').getByRole('heading', { name: 'Settings' })).toBeVisible()
  })

  test('header has "Save Settings" button', async ({ page }) => {
    await expect(page.getByRole('button', { name: /Save Settings/ })).toBeVisible()
  })

  test('shows LLM Configuration section', async ({ page }) => {
    await expect(page.getByText('LLM Configuration')).toBeVisible()
  })

  test('shows API Key field', async ({ page }) => {
    await expect(page.getByText('API Key')).toBeVisible()
    const input = page.locator('input[type="password"]')
    await expect(input).toBeVisible()
  })

  test('shows masked API key hint', async ({ page }) => {
    await expect(page.getByText('Saved (masked)')).toBeVisible()
  })

  test('shows Model field with value', async ({ page }) => {
    await expect(page.getByText('Model')).toBeVisible()
    const modelInput = page.locator('input[type="text"]').first()
    await expect(modelInput).toHaveValue('anthropic/claude-sonnet-4-20250514')
  })

  test('shows Base URL field', async ({ page }) => {
    await expect(page.getByText('Base URL (Optional)')).toBeVisible()
  })

  test('shows Default Executor dropdown', async ({ page }) => {
    await expect(page.getByText('Default Executor')).toBeVisible()
    const select = page.locator('select')
    await expect(select).toBeVisible()
    await expect(select).toHaveValue('claude_api')
  })

  test('executor dropdown shows description text', async ({ page }) => {
    await expect(page.getByText('Direct Claude API calls via LiteLLM')).toBeVisible()
  })

  test('changing executor updates description', async ({ page }) => {
    const select = page.locator('select')
    await select.selectOption('bsgateway')
    await expect(page.getByText('BSGateway proxy with cost tracking')).toBeVisible()
  })

  test('Settings nav item is active in sidebar', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const settingsLink = sidebar.getByRole('link', { name: 'Settings' })
    await expect(settingsLink).toHaveClass(/font-semibold/)
  })
})
