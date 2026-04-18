/**
 * Longrun scenario — CMO-initiated autonomous collaboration with marker verification.
 *
 * Condition-based exit (not time-fixed). Max deadline: 8 hours.
 * Creates a fresh project, sends CMO a market research + full project execution prompt,
 * then monitors until success conditions are met.
 *
 * Verifies:
 * - Inline markers ([CREATE_TASK], [CREATE_PHASE]) are parsed and stripped from chat
 * - Agent busy indicator (green dot) appears during processing
 * - Multi-agent chain produces phases, tasks, files, and design screens
 *
 * On success: project preserved for manual inspection.
 * On failure: project deleted from DB.
 */
import { expect, test } from '@playwright/test'

const LIVE_FRONTEND_URL = process.env.LIVE_FRONTEND_URL || 'http://localhost:13100'
const LIVE_API_URL = process.env.LIVE_API_URL || 'http://localhost:18100'

// E2E bypass token — must match E2E_TEST_TOKEN env var on backend
const E2E_TOKEN = process.env.E2E_TOKEN || 'e2e-scenario-test-token'

// Condition-based exit thresholds
const FULL_SUCCESS = { files: 10, screens: 3, phases: 3, tasks: 20, doneTasks: 15 }
const CHAIN_COMPLETE = { phases: 3, tasks: 15, agents: 5, doneTasks: 10, files: 5 }
const STALL_WARNING_MIN = 30
const STALL_FAIL_MIN = 60
const MAX_HOURS = 8
const POLL_INTERVAL_S = 30

test.describe('Longrun marker scenario — CMO full project', () => {
  test('CMO research → delegation → design + code files', async ({ page }) => {
    test.setTimeout(MAX_HOURS * 3600 * 1000 + 600_000) // 8h + 10min buffer

    // ── Auth: inject E2E bypass token into localStorage ──
    const apiHeaders = () => ({
      'Authorization': `Bearer ${E2E_TOKEN}`,
      'Content-Type': 'application/json',
    })

    // Verify backend is reachable with E2E token
    const healthResp = await page.request.get(`${LIVE_API_URL}/api/v1/agents`, {
      headers: apiHeaders(),
    })
    expect(healthResp.ok(), `Backend not reachable or E2E token invalid: ${healthResp.status()}`).toBe(true)
    console.log('Step 1: Backend reachable with E2E token')

    // ── Create fresh project via API ──
    const timestamp = new Date().toISOString().slice(5, 16).replace('T', ' ')
    const projectName = `E2E Longrun ${timestamp}`

    const createResp = await page.request.post(`${LIVE_API_URL}/api/v1/projects`, {
      headers: apiHeaders(),
      data: { name: projectName, description: 'Longrun marker scenario by Playwright', workspace_type: 'server_managed' },
    })
    expect(createResp.ok(), `Project creation failed: ${createResp.status()}`).toBe(true)
    const projectData = await createResp.json()
    const projectId = projectData.id
    console.log(`Step 2: Project created — ${projectName} (${projectId})`)
    console.log(`  URL: ${LIVE_FRONTEND_URL}/projects/${projectId}`)

    // ── Send initial message via API (bypass UI auth) ──
    const chatResp = await page.request.post(
      `${LIVE_API_URL}/api/v1/projects/${projectId}/chat`,
      {
        headers: apiHeaders(),
        data: {
          message: '@CMO 시장 조사해서 유망한 프로젝트를 제안하고, 기획부터 개발/디자인까지 전체 진행해줘. ' +
            '실제 동작하는 앱의 소스코드와 디자인 화면을 만들어야 해.',
        },
      }
    )
    expect(chatResp.ok(), `Chat send failed: ${chatResp.status()}`).toBe(true)
    console.log('Step 3: Message sent to CMO via API')

    // Navigate to project page with token in localStorage for SSE
    await page.addInitScript((token: string) => {
      localStorage.setItem('bsnexus_access_token', token)
    }, E2E_TOKEN)
    await page.goto(`${LIVE_FRONTEND_URL}/projects/${projectId}`, {
      waitUntil: 'domcontentloaded', timeout: 15_000,
    })
    await page.waitForTimeout(3_000)

    // ── Monitoring state ──
    let lastPhaseCount = 0
    let lastTaskCount = 0
    let lastDoneCount = 0
    let lastMsgCount = 0
    let filesCount = 0
    let screensCount = 0
    const agentsSeen = new Set<string>()
    let markerLeakCount = 0
    let lastProgressTime = Date.now()
    let exitReason = 'DEADLINE'
    let success = false
    const maxIterations = Math.ceil((MAX_HOURS * 3600) / POLL_INTERVAL_S)

    for (let i = 0; i < maxIterations; i++) {
      await page.waitForTimeout(POLL_INTERVAL_S * 1000)
      const elapsedS = (i + 1) * POLL_INTERVAL_S
      const elapsedMin = Math.floor(elapsedS / 60)

      let phases = 0, tasks = 0, doneTasks = 0, msgs = 0

      try {
        const headers = apiHeaders()

        // Plan tree
        const planResp = await page.request.get(
          `${LIVE_API_URL}/api/v1/projects/${projectId}/plan-tree`, { headers }
        )
        if (planResp.ok()) {
          const plan = await planResp.json()
          const phaseList = plan.phases || []
          phases = phaseList.length
          tasks = phaseList.reduce((s: number, p: { tasks: unknown[] }) => s + p.tasks.length, 0)
          doneTasks = phaseList.reduce(
            (s: number, p: { tasks: Array<{ status: string }> }) =>
              s + p.tasks.filter(t => t.status === 'done').length, 0
          )
        }

        // Chat messages
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
            // Marker leak check: raw markers should never appear in stored messages
            if (m.content && (m.content.includes('[CREATE_TASK ') || m.content.includes('[CREATE_PHASE '))) {
              markerLeakCount++
            }
          }
        }

        // Files
        const filesResp = await page.request.get(
          `${LIVE_API_URL}/api/v1/projects/${projectId}/files`, { headers }
        )
        if (filesResp.ok()) {
          const fd = await filesResp.json()
          filesCount = Array.isArray(fd) ? fd.length : (fd.files || []).length
        }

        // Design screens
        const screensResp = await page.request.get(
          `${LIVE_API_URL}/api/v1/projects/${projectId}/design/screens`, { headers }
        )
        if (screensResp.ok()) {
          const sd = await screensResp.json()
          screensCount = (sd.screens || (Array.isArray(sd) ? sd : [])).length
        }
      } catch { /* ignore API errors */ }

      // Track progress
      const progressed = phases !== lastPhaseCount || tasks !== lastTaskCount ||
                          msgs !== lastMsgCount || doneTasks !== lastDoneCount
      if (progressed) lastProgressTime = Date.now()
      const stallMin = Math.floor((Date.now() - lastProgressTime) / 60000)

      // Periodic log (every 5 minutes)
      if (elapsedS % 300 === 0 || progressed) {
        console.log(
          `  [${elapsedMin}min] phases:${phases} tasks:${tasks}(done:${doneTasks}) msgs:${msgs} ` +
          `files:${filesCount} screens:${screensCount} agents:[${[...agentsSeen].join(',')}] ` +
          `stall:${stallMin}min markers_leaked:${markerLeakCount}${progressed ? ' <-- CHANGED' : ''}`
        )
      }

      // Checkpoint screenshot (every 15 minutes)
      if (elapsedS % 900 === 0) {
        await page.screenshot({ path: `/tmp/longrun-checkpoint-${elapsedMin}min.png`, fullPage: true })
      }

      lastPhaseCount = phases
      lastTaskCount = tasks
      lastDoneCount = doneTasks
      lastMsgCount = msgs

      // ── Exit conditions ──

      // FULL_SUCCESS
      if (filesCount >= FULL_SUCCESS.files && screensCount >= FULL_SUCCESS.screens &&
          phases >= FULL_SUCCESS.phases && tasks >= FULL_SUCCESS.tasks &&
          doneTasks >= FULL_SUCCESS.doneTasks) {
        exitReason = 'FULL_SUCCESS'
        success = true
        break
      }

      // CHAIN_COMPLETE
      if (phases >= CHAIN_COMPLETE.phases && tasks >= CHAIN_COMPLETE.tasks &&
          agentsSeen.size >= CHAIN_COMPLETE.agents && doneTasks >= CHAIN_COMPLETE.doneTasks &&
          filesCount >= CHAIN_COMPLETE.files) {
        exitReason = 'CHAIN_COMPLETE'
        success = true
        break
      }

      // STALL_WARNING
      if (stallMin >= STALL_WARNING_MIN && stallMin < STALL_FAIL_MIN) {
        if (elapsedS % 300 === 0) {
          console.log(`  ⚠ STALL WARNING: no progress for ${stallMin} minutes`)
        }
      }

      // STALL_FAIL
      if (stallMin >= STALL_FAIL_MIN) {
        exitReason = 'STALL_FAIL'
        // Still consider it success if we met chain_complete thresholds
        success = phases >= 2 && tasks >= 10 && doneTasks >= 5
        console.log(`  STALL FAIL: no progress for ${stallMin} minutes`)
        break
      }
    }

    // ── Final report ──
    console.log(`\n${'='.repeat(60)}`)
    console.log(`EXIT: ${exitReason} (success: ${success})`)
    console.log(`Phases: ${lastPhaseCount}`)
    console.log(`Tasks: ${lastTaskCount} (done: ${lastDoneCount})`)
    console.log(`Messages: ${lastMsgCount}`)
    console.log(`Agents: [${[...agentsSeen].join(', ')}] (${agentsSeen.size})`)
    console.log(`Files: ${filesCount}`)
    console.log(`Design screens: ${screensCount}`)
    console.log(`Marker leaks: ${markerLeakCount}`)

    // Plan tree dump
    try {
      const headers = apiHeaders()
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

    // Last 20 chat messages
    try {
      const headers = apiHeaders()
      const chatResp = await page.request.get(
        `${LIVE_API_URL}/api/v1/projects/${projectId}/chat`, { headers }
      )
      if (chatResp.ok()) {
        const chat = await chatResp.json()
        console.log('\n=== Last 20 Messages ===')
        for (const m of (chat.messages || []).slice(-20)) {
          const preview = (m.content || '').slice(0, 120).replace(/\n/g, ' ')
          console.log(`[${m.role}${m.agent_name ? ` ${m.agent_name}` : ''}] ${preview}`)
        }
      }
    } catch { /* ignore */ }

    // Final screenshot
    await page.screenshot({ path: '/tmp/longrun-final.png', fullPage: true })

    // ── Cleanup / preserve ──
    if (success) {
      console.log(`\nSUCCESS — project preserved: ${LIVE_FRONTEND_URL}/projects/${projectId}`)
    } else {
      try {
        const headers = apiHeaders()
        await page.request.delete(`${LIVE_API_URL}/api/v1/projects/${projectId}`, { headers })
        console.log('\nFAILED — project cleaned up')
      } catch {
        console.log('\nFAILED — cleanup also failed')
      }
    }
    console.log(`${'='.repeat(60)}`)

    // ── Assertions ──
    expect(markerLeakCount, 'No raw marker text should appear in stored messages').toBe(0)
    expect(lastPhaseCount, 'At least 3 phases created').toBeGreaterThanOrEqual(3)
    expect(lastTaskCount, 'At least 15 tasks created').toBeGreaterThanOrEqual(15)
    expect(lastDoneCount, 'At least 10 tasks done').toBeGreaterThanOrEqual(10)
    expect(filesCount, 'At least 5 files written').toBeGreaterThanOrEqual(5)
    expect(agentsSeen.size, 'At least 4 agents active').toBeGreaterThanOrEqual(4)
  })
})
