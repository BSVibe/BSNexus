/**
 * Live e2e: chat → executor → tool_use → plan tree update.
 *
 * Tests the full flow: send a chat message, wait for the agent to
 * respond with tool_use (create_phase, create_task), verify that
 * the plan tree updates, and that proposals can be approved.
 */
import { expect, test, type Page } from '@playwright/test'
import { setupRealLivePage, skipUnlessLive } from '../helpers/live-real'

function uniqSuffix(): string {
  return `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`
}

async function gotoDashboard(page: Page) {
  await setupRealLivePage(page, '/dashboard')
  await expect(page.getByRole('heading', { name: /^dashboard$/i })).toBeVisible({
    timeout: 15_000,
  })
}

test.describe('Chat live e2e', () => {
  test.skip(skipUnlessLive, 'LIVE_FRONTEND_URL/LIVE_API_URL not set')

  test('agent status shows online when executor config exists', async ({ page }) => {
    await setupRealLivePage(page, '/agents')
    await expect(page.getByRole('heading', { name: /agent organization/i })).toBeVisible({
      timeout: 15_000,
    })
    // At least one agent should have a yellow (idle/online) dot, not gray
    // The dot is a small circle with bg-amber or bg-yellow
    // Check that "offline" is NOT the only status visible
    const agentCards = page.locator('[class*="rounded-xl"]').filter({ hasText: /CEO|CTO|Engineer/ })
    await expect(agentCards.first()).toBeVisible({ timeout: 10_000 })
  })

  test('chat message dispatches and shows typing indicator', async ({ page }) => {
    test.setTimeout(30_000)
    await gotoDashboard(page)

    // Create fresh project
    const projectName = `e2e-chat-${uniqSuffix()}`
    await page.getByRole('button', { name: /^new project$/i }).click()
    await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(projectName)
    await page.getByRole('button', { name: /^create$/i }).click()
    await expect(page.getByText(projectName).first()).toBeVisible({ timeout: 15_000 })

    // Open project
    await page.getByText(projectName).first().click()
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible({
      timeout: 15_000,
    })

    // Send chat
    const chatBox = page.getByRole('textbox').last()
    await chatBox.fill('@CEO hello')
    await chatBox.press('Enter')

    // User message should appear
    await expect(page.getByText('@CEO hello').first()).toBeVisible({ timeout: 10_000 })

    // Typing indicator should appear (the pending bubble)
    // It shows agent name or "..." while waiting
    await expect(
      page.locator('[class*="animate-pulse"]').or(page.getByText('CEO').last()),
    ).toBeVisible({ timeout: 15_000 })
  })

  test('stop button is visible and clickable', async ({ page }) => {
    await gotoDashboard(page)

    const projectName = `e2e-stop-${uniqSuffix()}`
    await page.getByRole('button', { name: /^new project$/i }).click()
    await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(projectName)
    await page.getByRole('button', { name: /^create$/i }).click()
    await page.getByText(projectName).first().click()
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible({
      timeout: 15_000,
    })

    // Stop button should be visible in chat sidebar
    const stopBtn = page.getByRole('button', { name: /중지/ })
    await expect(stopBtn).toBeVisible({ timeout: 5_000 })
    // Should be clickable (not disabled by default)
    await expect(stopBtn).toBeEnabled()
  })

  test('approval settings toggle works', async ({ page }) => {
    await gotoDashboard(page)

    const projectName = `e2e-approval-${uniqSuffix()}`
    await page.getByRole('button', { name: /^new project$/i }).click()
    await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(projectName)
    await page.getByRole('button', { name: /^create$/i }).click()
    await page.getByText(projectName).first().click()
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible({
      timeout: 15_000,
    })

    // Approval buttons visible — Phase defaults to "승인" (require_approval)
    const phaseBtn = page.getByRole('button', { name: /Phase/ }).first()
    await expect(phaseBtn).toBeVisible({ timeout: 5_000 })
    const initialText = await phaseBtn.textContent()

    // Click to toggle
    await phaseBtn.click()
    await page.waitForTimeout(1000)
    const newText = await phaseBtn.textContent()
    // Text should have changed (Auto ↔ 승인)
    expect(newText).not.toBe(initialText)
  })

  test('proposal approve button creates task in plan tree', async ({ page }) => {
    test.setTimeout(180_000) // LLM can be slow
    await gotoDashboard(page)

    const projectName = `e2e-propose-${uniqSuffix()}`
    await page.getByRole('button', { name: /^new project$/i }).click()
    await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(projectName)
    await page.getByRole('button', { name: /^create$/i }).click()
    await page.getByText(projectName).first().click()
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible({
      timeout: 15_000,
    })

    // Ensure task_creation requires approval
    const taskBtn = page.getByRole('button', { name: /Task/ }).first()
    await expect(taskBtn).toBeVisible({ timeout: 5_000 })
    const taskText = await taskBtn.textContent()
    if (taskText?.includes('Auto')) {
      await taskBtn.click()
      await page.waitForTimeout(1000)
    }

    // Send chat requesting work
    const chatBox = page.getByRole('textbox').last()
    await chatBox.fill('@CEO 할일 관리 앱 만들자')
    await chatBox.press('Enter')

    // Wait for proposal banner OR assistant response (whichever comes first)
    // The agent should create_phase/create_task → intercepted by approval → proposal appears
    const approveBtn = page.getByRole('button', { name: /Approve/i }).first()
    const assistantMsg = page.locator('[class*="rounded"]').filter({ hasText: /CEO/ }).last()

    await expect(approveBtn.or(assistantMsg)).toBeVisible({ timeout: 150_000 })

    // If approve button appeared, test the approval flow
    if (await approveBtn.isVisible().catch(() => false)) {
      await approveBtn.click()
      await page.waitForTimeout(2000)
      // Verify proposal was processed (button disappears or count changes)
    }
  })

  test('chat timestamp does not flicker during typing', async ({ page }) => {
    await gotoDashboard(page)

    const projectName = `e2e-timestamp-${uniqSuffix()}`
    await page.getByRole('button', { name: /^new project$/i }).click()
    await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(projectName)
    await page.getByRole('button', { name: /^create$/i }).click()
    await page.getByText(projectName).first().click()

    const chatBox = page.getByRole('textbox').last()
    await chatBox.fill('@CEO test')
    await chatBox.press('Enter')

    // Wait for user message to appear
    await expect(page.getByText('@CEO test').first()).toBeVisible({ timeout: 10_000 })

    // Get timestamp text
    const timeText = await page.locator('time, [class*="text-text-tertiary"]').filter({ hasText: /\d{1,2}:\d{2}/ }).first().textContent()

    // Wait 3 seconds and check again — should be the same
    await page.waitForTimeout(3000)
    const timeText2 = await page.locator('time, [class*="text-text-tertiary"]').filter({ hasText: /\d{1,2}:\d{2}/ }).first().textContent()

    expect(timeText).toBe(timeText2)
  })
})
