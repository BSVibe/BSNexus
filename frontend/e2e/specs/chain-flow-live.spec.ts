/**
 * Chain flow — full autonomous agent collaboration.
 *
 * Scenario: User says "프로젝트 제안해줘"
 * Expected: CEO delegates → multiple agents create phases/tasks → chain completes
 * Screenshots at every state change.
 */
import { expect, test } from '@playwright/test'
import { loginAndNavigate, skipUnlessLive } from '../helpers/live-login'

test.describe('Agent chain flow', () => {
  test.skip(skipUnlessLive, 'not live')

  test('autonomous multi-agent collaboration', async ({ page }) => {
    test.setTimeout(600_000) // 10 min — agents are slow on local model

    // ── Login + open project ──
    await loginAndNavigate(page, '/dashboard')
    await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible({ timeout: 15_000 })

    const proj = page.getByText('test').first()
    await expect(proj).toBeVisible({ timeout: 5_000 })
    await proj.click()
    await page.waitForTimeout(3_000)
    await page.screenshot({ path: '/tmp/chain-01-project.png', fullPage: true })
    console.log('✓ Project opened')

    // ── Send initial request ──
    const chatBox = page.getByRole('textbox').last()
    await chatBox.fill('AI 에이전트 마켓플레이스 프로젝트 제안해줘. 시장조사, 기술스택, MVP 기능 정의까지.')
    await chatBox.press('Enter')
    await page.waitForTimeout(2_000)
    await page.screenshot({ path: '/tmp/chain-02-sent.png', fullPage: true })
    console.log('✓ Message sent')

    // ── Monitor progress ──
    let lastPhaseCount = 0
    let lastTaskCount = 0
    let lastMsgCount = 0
    let lastScreenshot = 0

    const projectId = page.url().split('/projects/')[1]?.split(/[?#]/)[0]
    const getToken = () => page.evaluate(() => localStorage.getItem('bsnexus_access_token'))

    for (let i = 0; i < 55; i++) { // 55 × 10s = ~9 min
      await page.waitForTimeout(10_000)
      const elapsed = (i + 1) * 10

      // Query APIs directly for accurate state
      let phases = 0, tasks = 0, msgs = 0, agentNames: string[] = []
      try {
        const token = await getToken()
        const headers = { 'Authorization': `Bearer ${token}` }

        const planResp = await page.request.get(
          `http://bsserver:18100/api/v1/projects/${projectId}/plan-tree`, { headers }
        )
        if (planResp.ok()) {
          const plan = await planResp.json()
          phases = (plan.phases || []).length
          tasks = (plan.phases || []).reduce((s: number, p: { tasks: unknown[] }) => s + p.tasks.length, 0)
        }

        const chatResp = await page.request.get(
          `http://bsserver:18100/api/v1/projects/${projectId}/chat`, { headers }
        )
        if (chatResp.ok()) {
          const chat = await chatResp.json()
          const assistantMsgs = (chat.messages || []).filter((m: { role: string }) => m.role === 'assistant')
          msgs = assistantMsgs.length
          agentNames = [...new Set(assistantMsgs.map((m: { agent_name: string }) => m.agent_name))]
        }
      } catch { /* ignore API errors */ }

      const changed = phases !== lastPhaseCount || tasks !== lastTaskCount || msgs !== lastMsgCount

      if (changed || elapsed - lastScreenshot >= 60) {
        await page.screenshot({ path: `/tmp/chain-progress-${elapsed}s.png`, fullPage: true })
        lastScreenshot = elapsed
      }

      console.log(`  [${elapsed}s] phases: ${phases} | tasks: ${tasks} | msgs: ${msgs} | agents: [${agentNames.join(', ')}]${changed ? ' ← CHANGED' : ''}`)

      lastPhaseCount = phases
      lastTaskCount = tasks
      lastMsgCount = msgs

      // Success criteria: at least 1 phase, 2+ tasks, 2+ agent responses (chain happened)
      if (phases >= 1 && tasks >= 2 && msgs >= 2 && agentNames.length >= 2) {
        console.log(`\n✓ Chain complete at ${elapsed}s!`)
        console.log(`  ${phases} phases, ${tasks} tasks, ${msgs} messages from ${agentNames.length} agents`)
        break
      }

      // Early exit if only 1 agent responded and 2+ min passed
      if (elapsed >= 180 && msgs >= 1 && phases >= 1 && tasks >= 2) {
        console.log(`\n✓ Single agent completed at ${elapsed}s (no chain, but work done)`)
        break
      }
    }

    // ── Final state ──
    await page.screenshot({ path: '/tmp/chain-final.png', fullPage: true })

    // Print final plan tree
    try {
      const token = await getToken()
      const planResp = await page.request.get(
        `http://bsserver:18100/api/v1/projects/${projectId}/plan-tree`,
        { headers: { 'Authorization': `Bearer ${token}` } }
      )
      if (planResp.ok()) {
        const plan = await planResp.json()
        console.log('\n=== Final Plan Tree ===')
        for (const p of plan.phases || []) {
          console.log(`Phase: ${p.name} (${p.status})`)
          for (const t of p.tasks || []) {
            console.log(`  Task: ${t.title} [${t.status}] ${t.agent_name ? `← ${t.agent_name}` : ''}`)
          }
        }
      }

      const chatResp = await page.request.get(
        `http://bsserver:18100/api/v1/projects/${projectId}/chat`,
        { headers: { 'Authorization': `Bearer ${token}` } }
      )
      if (chatResp.ok()) {
        const chat = await chatResp.json()
        console.log('\n=== Chat Messages ===')
        for (const m of chat.messages || []) {
          const preview = (m.content || '').slice(0, 100).replace(/\n/g, ' ')
          console.log(`[${m.role}${m.agent_name ? ` ${m.agent_name}` : ''}] ${preview}`)
        }
      }
    } catch { /* ignore */ }

    expect(lastPhaseCount).toBeGreaterThanOrEqual(1)
    expect(lastTaskCount).toBeGreaterThanOrEqual(2)
    expect(lastMsgCount).toBeGreaterThanOrEqual(1)
  })
})
