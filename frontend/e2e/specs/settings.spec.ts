import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

test.describe('Settings Page — Executor Configs', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/settings')
  })

  test('header displays "Settings" title', async ({ page }) => {
    await expect(page.locator('header').getByRole('heading', { name: 'Settings' })).toBeVisible()
  })

  test('header has "Register Executor" button', async ({ page }) => {
    await expect(page.getByRole('button', { name: /Register Executor/ })).toBeVisible()
  })

  test('shows "Registered Executors" section', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Registered Executors' })).toBeVisible()
  })

  test('renders executor config cards from API', async ({ page }) => {
    await expect(page.getByText('Claude Sonnet 4')).toBeVisible()
    await expect(page.getByText('Worker: Mac Mini Runner')).toBeVisible()
  })

  test('marks default executor with DEFAULT badge', async ({ page }) => {
    await expect(page.getByText('DEFAULT', { exact: true })).toBeVisible()
  })

  test('shows executor type labels on cards', async ({ page }) => {
    await expect(page.getByText('LLM API').first()).toBeVisible()
    await expect(page.getByText('Worker').first()).toBeVisible()
  })

  test('worker executor card has edit button', async ({ page }) => {
    // Find the card heading (h4) then walk up to the card container
    const workerHeading = page.getByRole('heading', { name: 'Worker: Mac Mini Runner' })
    const workerCard = workerHeading.locator('xpath=ancestor::div[contains(@class, "rounded-xl")][1]')
    await expect(workerCard.locator('button[title="Edit"]')).toBeVisible()
  })

  test('clicking Register Executor opens modal', async ({ page }) => {
    await page.getByRole('button', { name: /Register Executor/ }).click()
    await expect(page.getByRole('heading', { name: 'Register Executor' })).toBeVisible()
  })

  test('modal has executor type selector', async ({ page }) => {
    await page.getByRole('button', { name: /Register Executor/ }).click()
    const select = page.locator('select').first()
    await expect(select).toBeVisible()
    await expect(select).toHaveValue('claude_api')
  })

  test('Register button is disabled when name is empty', async ({ page }) => {
    await page.getByRole('button', { name: /Register Executor/ }).click()
    const registerBtn = page.getByRole('button', { name: 'Register', exact: true })
    await expect(registerBtn).toBeDisabled()
  })

  test('Register button enables after typing name', async ({ page }) => {
    await page.getByRole('button', { name: /Register Executor/ }).click()
    await page.getByPlaceholder('e.g. GPT-4o Production').fill('Test Config')
    const registerBtn = page.getByRole('button', { name: 'Register', exact: true })
    await expect(registerBtn).toBeEnabled()
  })

  test('selecting Self-Hosted Worker shows install guide', async ({ page }) => {
    await page.getByRole('button', { name: /Register Executor/ }).click()
    await page.locator('select').first().selectOption('_worker')
    await expect(page.getByText(/Install worker/i)).toBeVisible()
  })

  test('clicking edit on existing config opens edit modal', async ({ page }) => {
    const llmHeading = page.getByRole('heading', { name: 'Claude Sonnet 4' })
    const llmCard = llmHeading.locator('xpath=ancestor::div[contains(@class, "rounded-xl")][1]')
    await llmCard.locator('button[title="Edit"]').click()
    await expect(page.getByRole('heading', { name: /Edit — Claude Sonnet 4/ })).toBeVisible()
  })

  test('shows Worker Install Token section', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Worker Install Token' })).toBeVisible()
  })

  test('Settings nav item is active in sidebar', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const settingsLink = sidebar.getByRole('link', { name: 'Settings' })
    await expect(settingsLink).toHaveClass(/font-semibold/)
  })
})
