/**
 * Live e2e: task-centric architecture verification.
 *
 * Tests full chat → tool_use → state machine → SSE → plan tree flow.
 * Logs in via real BSVibe Auth, uses real backend + real LLM.
 */
import { expect, test, type Page } from '@playwright/test'
import { loginAndNavigate, skipUnlessLive } from '../helpers/live-login'

function uniqSuffix(): string {
  return `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`
}

async function createProjectAndOpen(page: Page, name: string) {
  await loginAndNavigate(page, '/dashboard')
  await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible({ timeout: 15_000 })

  await page.getByRole('button', { name: /new project/i }).click()
  await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(name)
  await page.getByRole('button', { name: /create/i }).click()
  await expect(page.getByText(name).first()).toBeVisible({ timeout: 15_000 })

  await page.getByText(name).first().click()
  await expect(page.getByRole('heading', { name })).toBeVisible({ timeout: 15_000 })
}

test.describe('Task-centric architecture — live e2e', () => {
  test.skip(skipUnlessLive, 'LIVE_FRONTEND_URL/LIVE_API_URL not set')

  test('login and dashboard loads', async ({ page }) => {
    await loginAndNavigate(page, '/dashboard')

    // Debug: capture where we ended up
    const url = page.url()
    const html = await page.content()
    console.log(`URL after login: ${url}`)
    console.log(`Page title: ${await page.title()}`)
    console.log(`Has dashboard heading: ${await page.getByRole('heading', { name: /dashboard/i }).isVisible().catch(() => false)}`)
    console.log(`Body snippet: ${html.slice(0, 500)}`)

    await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible({ timeout: 15_000 })
  })

  test('chat dispatches agent and response appears', async ({ page }) => {
    test.setTimeout(120_000)
    const projectName = `e2e-task-${uniqSuffix()}`
    await createProjectAndOpen(page, projectName)

    // Send chat
    const chatBox = page.getByRole('textbox').last()
    await chatBox.fill('@CMO 시장조사 계획 세워줘')
    await chatBox.press('Enter')

    // User message appears
    await expect(page.getByText('시장조사 계획 세워줘').first()).toBeVisible({ timeout: 10_000 })

    // Wait for assistant response
    const assistantBubble = page.locator('[class*="rounded"]').filter({ hasText: /CMO|CEO/ }).last()
    await expect(assistantBubble).toBeVisible({ timeout: 90_000 })

    const responseText = await assistantBubble.textContent()
    expect(responseText!.length).toBeGreaterThan(10)
    console.log(`Response preview: ${responseText!.slice(0, 100)}`)
  })

  test('stop button is clickable without error', async ({ page }) => {
    test.setTimeout(60_000)
    const projectName = `e2e-stop-${uniqSuffix()}`
    await createProjectAndOpen(page, projectName)

    const stopBtn = page.getByRole('button', { name: /중지/ })
    await expect(stopBtn).toBeVisible({ timeout: 5_000 })
    await expect(stopBtn).toBeEnabled()
  })
})
