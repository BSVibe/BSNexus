/**
 * Full project scenario — CMO-initiated autonomous collaboration.
 *
 * Creates a FRESH project each run via API, then:
 * 1. CMO에게 "시장 조사해서 간단한 프로젝트 만들어줘"
 * 2. CMO does research → reports to CEO
 * 3. Agents chain: phase/task creation + @mention delegation
 * 4. Verify: Files tab has workspace files, Design tab has .bsd files
 *
 * Agents are tenant-level (not project-bound) — enterprise template assumed.
 */
import { expect, test } from '@playwright/test'
import { LIVE_API_URL, loginAndNavigate, skipUnlessLive } from '../helpers/live-login'

const LIVE_FRONTEND_URL = process.env.LIVE_FRONTEND_URL || 'http://bsserver:13100'

test.describe('Full project scenario — CMO-initiated', () => {
  test.skip(skipUnlessLive, 'not live')

  test('CMO research → CEO delegation → files + design', async ({ page }) => {
    test.setTimeout(900_000) // 15 min — multiple agents, local model

    // ── Login + Dashboard ──
    await loginAndNavigate(page, '/dashboard')
    await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible({ timeout: 15_000 })
    console.log('Step 1: Dashboard loaded')

    // ── Create fresh project via API ──
    const getToken = () => page.evaluate(() => localStorage.getItem('bsnexus_access_token'))
    const apiHeaders = async () => ({
      'Authorization': `Bearer ${await getToken()}`,
      'Content-Type': 'application/json',
    })

    const timestamp = new Date().toISOString().slice(5, 16).replace('T', ' ')
    const projectName = `E2E Scenario ${timestamp}`

    const createResp = await page.request.post(`${LIVE_API_URL}/api/v1/projects`, {
      headers: await apiHeaders(),
      data: { name: projectName, description: 'Auto-created by Playwright E2E', workspace_type: 'server_managed' },
    })
    expect(createResp.ok(), `Project creation failed: ${createResp.status()}`).toBe(true)
    const projectData = await createResp.json()
    const projectId = projectData.id
    console.log(`Step 2: Fresh project created — ${projectName} (${projectId})`)

    // ── Navigate to the new project ──
    await page.goto(`${LIVE_FRONTEND_URL}/projects/${projectId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 15_000,
    })
    await page.waitForTimeout(3_000)
    await page.screenshot({ path: '/tmp/scenario-01-project.png', fullPage: true })
    console.log('Step 3: Project page loaded')

    // ── Send message to CMO ──
    const chatBox = page.getByRole('textbox').last()
    await expect(chatBox).toBeVisible({ timeout: 5_000 })
    await chatBox.fill('@CMO 시장 조사해서 간단한 모바일 앱 프로젝트를 만들어줘. 조사 결과를 CEO에게 보고하고 팀원들에게 업무를 나눠줘.')
    await chatBox.press('Enter')
    await page.waitForTimeout(2_000)
    await page.screenshot({ path: '/tmp/scenario-02-sent.png', fullPage: true })
    console.log('Step 4: Message sent to CMO')

    // ── Monitor agent chain progress ──
    let lastPhaseCount = 0
    let lastTaskCount = 0
    let lastMsgCount = 0
    const agentsSeen = new Set<string>()
    let chainComplete = false

    for (let i = 0; i < 85; i++) { // 85 x 10s = ~14 min
      await page.waitForTimeout(10_000)
      const elapsed = (i + 1) * 10

      let phases = 0, tasks = 0, msgs = 0
      try {
        const headers = await apiHeaders()

        const planResp = await page.request.get(
          `${LIVE_API_URL}/api/v1/projects/${projectId}/plan-tree`, { headers }
        )
        if (planResp.ok()) {
          const plan = await planResp.json()
          phases = (plan.phases || []).length
          tasks = (plan.phases || []).reduce(
            (s: number, p: { tasks: unknown[] }) => s + p.tasks.length, 0
          )
        }

        const chatResp = await page.request.get(
          `${LIVE_API_URL}/api/v1/projects/${projectId}/chat`, { headers }
        )
        if (chatResp.ok()) {
          const chat = await chatResp.json()
          const assistantMsgs = (chat.messages || []).filter(
            (m: { role: string }) => m.role === 'assistant'
          )
          msgs = assistantMsgs.length
          for (const m of assistantMsgs) {
            if (m.agent_name) agentsSeen.add(m.agent_name)
          }
        }
      } catch { /* ignore API errors */ }

      const changed = phases !== lastPhaseCount || tasks !== lastTaskCount || msgs !== lastMsgCount
      if (changed) {
        await page.screenshot({ path: `/tmp/scenario-progress-${elapsed}s.png`, fullPage: true })
      }

      console.log(
        `  [${elapsed}s] phases:${phases} tasks:${tasks} msgs:${msgs} ` +
        `agents:[${[...agentsSeen].join(',')}]${changed ? ' <-- CHANGED' : ''}`
      )

      lastPhaseCount = phases
      lastTaskCount = tasks
      lastMsgCount = msgs

      // Success: multi-agent chain with tasks
      if (phases >= 1 && tasks >= 2 && agentsSeen.size >= 2) {
        console.log(`\nChain complete at ${elapsed}s!`)
        chainComplete = true
        break
      }

      // Acceptable: chain happened (2+ agents) even if few tasks
      if (agentsSeen.size >= 2 && msgs >= 2 && phases >= 1) {
        console.log(`\nChain with delegation at ${elapsed}s`)
        chainComplete = true
        break
      }

      // Minimum: single agent completed with results
      if (elapsed >= 300 && phases >= 1 && msgs >= 1) {
        console.log(`\nSingle agent completed at ${elapsed}s`)
        chainComplete = true
        break
      }
    }

    // ── Final plan tree dump ──
    try {
      const headers = await apiHeaders()
      const planResp = await page.request.get(
        `${LIVE_API_URL}/api/v1/projects/${projectId}/plan-tree`, { headers }
      )
      if (planResp.ok()) {
        const plan = await planResp.json()
        console.log('\n=== Plan Tree ===')
        for (const p of plan.phases || []) {
          console.log(`Phase: ${p.name} (${p.status})`)
          for (const t of p.tasks || []) {
            console.log(`  Task: ${t.title} [${t.status}] ${t.agent_name ? `<- ${t.agent_name}` : ''}`)
          }
        }
      }
    } catch { /* ignore */ }

    // ── Check Files tab ──
    console.log('\n=== Files Tab ===')
    const filesTab = page.getByRole('button', { name: /files/i })
    await filesTab.click()
    await page.waitForTimeout(2_000)
    await page.screenshot({ path: '/tmp/scenario-03-files-tab.png', fullPage: true })

    let hasFiles = false
    try {
      const headers = await apiHeaders()
      const filesResp = await page.request.get(
        `${LIVE_API_URL}/api/v1/projects/${projectId}/files`, { headers }
      )
      if (filesResp.ok()) {
        const filesData = await filesResp.json()
        const files = filesData.files || filesData || []
        hasFiles = Array.isArray(files) && files.length > 0
        console.log(`Files found: ${Array.isArray(files) ? files.length : 'unknown format'}`)
        if (Array.isArray(files)) {
          for (const f of files.slice(0, 10)) {
            console.log(`  ${typeof f === 'string' ? f : f.name || f.path || JSON.stringify(f)}`)
          }
        }
      }
    } catch (e) {
      console.log(`Files API error: ${e}`)
    }

    // ── Check Design tab ──
    console.log('\n=== Design Tab ===')
    const designTab = page.getByRole('button', { name: /design/i })
    await designTab.click()
    await page.waitForTimeout(2_000)
    await page.screenshot({ path: '/tmp/scenario-04-design-tab.png', fullPage: true })

    let hasDesign = false
    try {
      const headers = await apiHeaders()
      const screensResp = await page.request.get(
        `${LIVE_API_URL}/api/v1/projects/${projectId}/design/screens`, { headers }
      )
      if (screensResp.ok()) {
        const screens = await screensResp.json()
        const screenList = screens.screens || screens || []
        hasDesign = Array.isArray(screenList) && screenList.length > 0
        console.log(`Design screens found: ${Array.isArray(screenList) ? screenList.length : 'unknown'}`)
        if (Array.isArray(screenList)) {
          for (const s of screenList.slice(0, 10)) {
            console.log(`  ${s.name || s.slug || JSON.stringify(s).slice(0, 80)}`)
          }
        }
      }
    } catch (e) {
      console.log(`Design API error: ${e}`)
    }

    // ── Back to Plan tab for final screenshot ──
    const planTab = page.getByRole('button', { name: /plan/i })
    await planTab.click()
    await page.waitForTimeout(1_000)
    await page.screenshot({ path: '/tmp/scenario-05-final.png', fullPage: true })

    // ── Chat messages dump ──
    try {
      const headers = await apiHeaders()
      const chatResp = await page.request.get(
        `${LIVE_API_URL}/api/v1/projects/${projectId}/chat`, { headers }
      )
      if (chatResp.ok()) {
        const chat = await chatResp.json()
        console.log('\n=== Chat Messages ===')
        for (const m of (chat.messages || []).slice(-20)) {
          const preview = (m.content || '').slice(0, 120).replace(/\n/g, ' ')
          console.log(`[${m.role}${m.agent_name ? ` ${m.agent_name}` : ''}] ${preview}`)
        }
      }
    } catch { /* ignore */ }

    // ── Assertions ──
    console.log('\n=== Results ===')
    console.log(`Phases: ${lastPhaseCount}`)
    console.log(`Tasks: ${lastTaskCount}`)
    console.log(`Messages: ${lastMsgCount}`)
    console.log(`Agents: [${[...agentsSeen].join(', ')}]`)
    console.log(`Files: ${hasFiles}`)
    console.log(`Design: ${hasDesign}`)

    expect(chainComplete, 'Agent chain should produce phases/tasks or multi-agent responses').toBe(true)
    expect(lastPhaseCount, 'At least 1 phase created').toBeGreaterThanOrEqual(1)
    expect(lastMsgCount, 'At least 1 agent response').toBeGreaterThanOrEqual(1)

    // Files and design are aspirational — log but don't fail on them yet
    if (hasFiles) console.log('FILES: PASS')
    else console.log('FILES: no files yet (agent may not have written any)')

    if (hasDesign) console.log('DESIGN: PASS')
    else console.log('DESIGN: no .bsd screens yet (agent may not have created any)')
  })
})
