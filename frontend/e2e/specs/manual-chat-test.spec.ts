/**
 * Manual chat test — real login, real project, real LLM.
 * Takes screenshots at every step for verification.
 */
import { expect, test } from '@playwright/test'
import { loginAndNavigate, skipUnlessLive } from '../helpers/live-login'

test.describe('Manual chat test', () => {
  test.skip(skipUnlessLive, 'not live')

  test('full chat flow with screenshots', async ({ page }) => {
    test.setTimeout(300_000) // 5 min for slow local model

    // Step 1: Login
    await loginAndNavigate(page, '/dashboard')
    await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible({ timeout: 15_000 })
    await page.screenshot({ path: '/tmp/pw-step1-dashboard.png', fullPage: true })
    console.log('Step 1: Dashboard loaded ✓')

    // Step 2: Open test project
    const testProject = page.getByText('test').first()
    if (await testProject.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await testProject.click()
    } else {
      // Create new project
      await page.getByRole('button', { name: /new project/i }).click()
      await page.getByPlaceholder('e.g. BSNexus Mobile App').fill('test')
      await page.getByRole('button', { name: /create/i }).click()
      await page.waitForTimeout(2_000)
      await page.getByText('test').first().click()
    }
    await page.waitForTimeout(3_000)
    await page.screenshot({ path: '/tmp/pw-step2-project.png', fullPage: true })
    console.log(`Step 2: Project page loaded ✓ (${page.url()})`)

    // Step 3: Send chat message
    const chatBox = page.getByRole('textbox').last()
    await expect(chatBox).toBeVisible({ timeout: 5_000 })
    await chatBox.fill('@CMO 시장조사해서 간단한 프로젝트 제안해줘')
    await chatBox.press('Enter')
    await page.waitForTimeout(2_000)
    await page.screenshot({ path: '/tmp/pw-step3-sent.png', fullPage: true })
    console.log('Step 3: Chat message sent ✓')

    // Step 4: Wait for assistant response (poll every 10s, up to 240s)
    let foundResponse = false
    for (let i = 0; i < 24; i++) {
      await page.waitForTimeout(10_000)
      await page.screenshot({ path: `/tmp/pw-step4-wait-${(i+1)*10}s.png`, fullPage: true })

      // Look for assistant message in chat — any element with agent name + substantial text
      const chatArea = page.locator('[class*="overflow-y"]').last()
      const allText = await chatArea.textContent().catch(() => '')

      // Check if there's text after the user message that's not just UI chrome
      const hasResponse = allText.includes('프로젝트') || allText.includes('조사') || allText.includes('Phase')
      const msgCount = (allText.match(/CMO|CEO/g) || []).length

      console.log(`  [${(i+1)*10}s] Chat text length: ${allText.length}, Agent mentions: ${msgCount}`)

      if (allText.length > 200 && msgCount >= 2) {
        foundResponse = true
        console.log(`Step 4: Agent response detected at ${(i+1)*10}s ✓`)
        break
      }
    }

    await page.screenshot({ path: '/tmp/pw-step5-final.png', fullPage: true })

    if (!foundResponse) {
      console.log('Step 4: No agent response after 240s ✗')
    }

    // Step 5: Check plan tree
    const pageContent = await page.content()
    const hasPlanNodes = pageContent.includes('pending') || pageContent.includes('running')
    console.log(`Step 5: Plan tree has nodes: ${hasPlanNodes}`)

    expect(foundResponse).toBe(true)
  })
})
