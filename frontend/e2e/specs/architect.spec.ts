import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

test.describe('Architect — Chat Interface & Sessions', () => {
  test('shows empty state with BSNexus Architect branding', async ({ page }) => {
    await setupPage(page, '/architect')
    await expect(page.getByText('BSNexus Architect').first()).toBeVisible()
    await expect(page.getByText('Select a session from the sidebar or create a new one')).toBeVisible()
  })

  test('empty state shows psychology icon (Material Symbols)', async ({ page }) => {
    await setupPage(page, '/architect')
    await expect(page.locator('span.material-symbols-outlined:has-text("psychology")')).toBeVisible()
  })

  test('empty state shows AI-powered design badge', async ({ page }) => {
    await setupPage(page, '/architect')
    await expect(page.getByText('AI-powered design')).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("auto_awesome")')).toBeVisible()
  })

  test('session list sidebar renders with Sessions heading', async ({ page }) => {
    await setupPage(page, '/architect')
    await expect(page.getByText('Sessions').first()).toBeVisible()
  })

  test('session list shows existing sessions', async ({ page }) => {
    await setupPage(page, '/architect')
    await expect(page.getByText('BSNexus design session')).toBeVisible()
    await expect(page.getByText('Exploring new ideas')).toBeVisible()
  })

  test('session list has "New" button with gradient styling and add icon', async ({ page }) => {
    await setupPage(page, '/architect')
    const newBtn = page.getByRole('button', { name: 'add New' })
    await expect(newBtn).toBeVisible()
    await expect(newBtn.locator('span.material-symbols-outlined:has-text("add")')).toBeVisible()
  })

  test('clicking session loads chat messages', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    await expect(page.getByText('I want to build an AI-powered development management system')).toBeVisible()
    await expect(page.getByText('I will design a system with the following components')).toBeVisible()
  })

  test('active session shows connection status badge', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    await expect(page.getByText('Connected')).toBeVisible()
  })

  test('active session shows session name in header', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    await expect(page.getByText('BSNexus design session').first()).toBeVisible()
  })

  test('chat input has toolbar with Architect label and icons', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    const input = page.locator('div').filter({ hasText: 'Architect' }).first()
    await expect(input).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("model_training")')).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("attach_file")')).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("image")')).toBeVisible()
  })

  test('chat input shows placeholder text', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    await expect(page.getByPlaceholder('Orchestrate your next move...')).toBeVisible()
  })

  test('chat input has send button with Material Symbol', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    await expect(page.locator('span.material-symbols-outlined:has-text("send")')).toBeVisible()
  })

  test('chat input shows "MD SUPPORTED" label', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    await expect(page.getByText('MD SUPPORTED')).toBeVisible()
  })

  test('empty session shows bolt icon prompt', async ({ page }) => {
    await setupPage(page, '/architect/session-002')
    await expect(page.locator('span.material-symbols-outlined:has-text("bolt")')).toBeVisible()
    await expect(page.getByText("Describe the project you'd like to build.")).toBeVisible()
  })

  test('header shows account_tree and more_vert icons', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    await expect(page.locator('button span.material-symbols-outlined:has-text("account_tree")')).toBeVisible()
    await expect(page.locator('button span.material-symbols-outlined:has-text("more_vert")')).toBeVisible()
  })

  test('AI disclaimer text is visible below input', async ({ page }) => {
    await setupPage(page, '/architect/session-001')
    await expect(page.getByText('AI can hallucinate')).toBeVisible()
  })
})
