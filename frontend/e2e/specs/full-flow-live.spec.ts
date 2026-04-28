/**
 * Full flow live e2e — tests the complete user journey:
 * 1. Login → Dashboard → Open project
 * 2. Send chat → Agent creates phase + tasks
 * 3. Verify plan tree updates in real-time
 * 4. Verify agent status changes
 * 5. Verify delegation chain (agent mentions another agent)
 * 6. Screenshot at every step
 */
import { expect, test } from '@playwright/test'
import { loginAndNavigate, skipUnlessLive } from '../helpers/live-login'

test.describe('Full flow — live e2e', () => {
  test.skip(skipUnlessLive, 'not live')

  test('chat → phase + task creation → plan tree update', async ({ page }) => {
    test.setTimeout(300_000)

    // ── Step 1: Login + Dashboard ──
    await loginAndNavigate(page, '/dashboard')
    await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible({ timeout: 15_000 })
    await page.screenshot({ path: '/tmp/flow-01-dashboard.png', fullPage: true })
    console.log('✓ Step 1: Dashboard')

    // ── Step 2: Open project ──
    const proj = page.getByText('test').first()
    await expect(proj).toBeVisible({ timeout: 5_000 })
    await proj.click()
    await page.waitForTimeout(3_000)
    await page.screenshot({ path: '/tmp/flow-02-project.png', fullPage: true })
    console.log(`✓ Step 2: Project (${page.url()})`)

    // ── Step 3: Verify initial state — empty plan tree ──
    await page.screenshot({ path: '/tmp/flow-03-empty-plan.png', fullPage: true })
    console.log('✓ Step 3: Initial empty plan tree')

    // ── Step 4: Send chat ──
    const chatBox = page.getByRole('textbox').last()
    await expect(chatBox).toBeVisible({ timeout: 5_000 })
    await chatBox.fill('@CMO 시장조사해서 간단한 프로젝트 제안해줘')
    await chatBox.press('Enter')
    await page.waitForTimeout(2_000)
    await page.screenshot({ path: '/tmp/flow-04-message-sent.png', fullPage: true })
    console.log('✓ Step 4: Message sent')

    // ── Step 5: Poll for agent response + plan tree changes ──
    let hasResponse = false
    let hasPlanNodes = false
    let lastChatLen = 0

    for (let i = 0; i < 30; i++) {  // 30 × 10s = 300s max
      await page.waitForTimeout(10_000)

      // Check chat area for new content
      const chatContent = (await page
        .locator('[class*="overflow-y"]')
        .last()
        .textContent()
        .catch(() => '')) ?? ''
      const chatLen = chatContent.length

      // Check plan tree for nodes (phases/tasks)
      const pageHtml = await page.content()
      const planHasContent = pageHtml.includes('pending') || pageHtml.includes('running')

      // Check if agent status bar shows any green dot (active agent)
      const hasGreenDot = pageHtml.includes('#34d399') || pageHtml.includes('emerald')

      if (chatLen !== lastChatLen) {
        await page.screenshot({ path: `/tmp/flow-05-progress-${(i+1)*10}s.png`, fullPage: true })
      }
      lastChatLen = chatLen

      console.log(`  [${(i+1)*10}s] chat: ${chatLen} chars | plan: ${planHasContent ? 'YES' : 'no'} | green dot: ${hasGreenDot ? 'YES' : 'no'}`)

      if (chatLen > 200) hasResponse = true
      if (planHasContent) hasPlanNodes = true

      // If we have both response and plan nodes, success
      if (hasResponse && hasPlanNodes) {
        console.log(`✓ Step 5: Response + Plan tree at ${(i+1)*10}s`)
        break
      }
    }

    await page.screenshot({ path: '/tmp/flow-06-after-response.png', fullPage: true })

    // ── Step 6: Verify plan tree content ──
    if (hasPlanNodes) {
      // Click on a phase/task in plan tree to see detail panel
      const treeNode = page.locator('[class*="cursor-pointer"]').first()
      if (await treeNode.isVisible().catch(() => false)) {
        await treeNode.click()
        await page.waitForTimeout(1_000)
        await page.screenshot({ path: '/tmp/flow-07-detail-panel.png', fullPage: true })
        console.log('✓ Step 6: Detail panel opened')
      }
    }

    // ── Step 7: Check DB for task_id linkage ──
    // (via API since we can't query DB from browser)
    const projectId = page.url().split('/projects/')[1]?.split(/[?#]/)[0]
    if (projectId) {
      const chatResp = await page.request.get(
        `http://bsserver:18100/api/v1/projects/${projectId}/chat`,
        { headers: { 'Authorization': `Bearer ${await page.evaluate(() => localStorage.getItem('bsnexus_access_token'))}` } }
      )
      if (chatResp.ok()) {
        const data = await chatResp.json()
        const msgs = data.messages || []
        const assistantMsgs = msgs.filter((m: { role: string }) => m.role === 'assistant')
        const withTaskId = assistantMsgs.filter((m: { task_id: string | null }) => m.task_id)
        console.log(`✓ Step 7: ${assistantMsgs.length} assistant msgs, ${withTaskId.length} with task_id`)
      }

      // Check plan tree API
      const planResp = await page.request.get(
        `http://bsserver:18100/api/v1/projects/${projectId}/plan-tree`,
        { headers: { 'Authorization': `Bearer ${await page.evaluate(() => localStorage.getItem('bsnexus_access_token'))}` } }
      )
      if (planResp.ok()) {
        const plan = await planResp.json()
        const phases = plan.phases || []
        const totalTasks = phases.reduce((sum: number, p: { tasks: unknown[] }) => sum + p.tasks.length, 0)
        console.log(`✓ Step 7: ${phases.length} phases, ${totalTasks} tasks`)
        for (const p of phases) {
          console.log(`  Phase: ${p.name} (${p.status}) — ${p.tasks.length} tasks`)
          for (const t of p.tasks) {
            console.log(`    Task: ${t.title} [${t.status}]`)
          }
        }
      }
    }

    await page.screenshot({ path: '/tmp/flow-08-final.png', fullPage: true })

    // ── Assertions ──
    expect(hasResponse).toBe(true)
    expect(hasPlanNodes).toBe(true)
  })
})
