/**
 * Long-term UI stability test — Playwright-based repeated scenario cycles.
 *
 * Runs multiple project creation → chat → verify cycles through the actual UI.
 * Each cycle:
 *   1. Navigate to dashboard, create project
 *   2. Send chat to CMO via chat input
 *   3. Wait for plan tree to populate (tasks appear)
 *   4. Verify: no duplicates, no self-assign
 *   5. Click stop-all, verify tasks transition
 *   6. Click restart, verify tasks resume
 *   7. Log results
 *
 * Designed to run 8-10 hours unattended via:
 *   LIVE_FRONTEND_URL=http://localhost:13100 LIVE_API_URL=http://localhost:18100 \
 *   npx playwright test e2e/specs/longterm-ui-live.spec.ts --config=playwright.s5.config.ts
 */
import { expect, test, type Page } from '@playwright/test'

const FE_URL = process.env.LIVE_FRONTEND_URL || 'http://localhost:13100'
const API_URL = process.env.LIVE_API_URL || 'http://localhost:18100'
const BYPASS_TOKEN = 'e2e-scenario-test-token'
const MAX_CYCLES = parseInt(process.env.LONGTERM_CYCLES || '32', 10)
const TASK_WAIT_MS = parseInt(process.env.TASK_WAIT_MS || '300000', 10) // 5min: tasks appear
const DONE_WAIT_MS = parseInt(process.env.DONE_WAIT_MS || '900000', 10) // 15min: at least one task done + files

function buildFakeJwt(): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const payload = btoa(
    JSON.stringify({
      sub: 'e2e-test-user',
      email: 'e2e@bsnexus.test',
      exp: 9999999999,
      app_metadata: {
        tenant_id: 'ab8bfb15-cb63-4068-a600-54b02b33396d',
        role: 'admin',
      },
    }),
  )
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '')
  return `${header}.${payload}.e2e-sig`
}

async function setupAuth(page: Page) {
  const fakeJwt = buildFakeJwt()
  await page.route('**/auth.bsvibe.dev/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: fakeJwt, refresh_token: 'fake', expires_in: 99999 }),
    }),
  )
  await page.addInitScript(
    ({ token }) => localStorage.setItem('bsnexus_access_token', token),
    { token: fakeJwt },
  )
  await page.route(`${FE_URL}/api/**`, async (route) => {
    const headers = { ...route.request().headers(), authorization: `Bearer ${BYPASS_TOKEN}` }
    await route.continue({ headers })
  })
}

async function apiCall(method: string, path: string, body?: object) {
  const opts: RequestInit = {
    method,
    headers: { Authorization: `Bearer ${BYPASS_TOKEN}`, 'Content-Type': 'application/json' },
  }
  if (body) opts.body = JSON.stringify(body)
  const resp = await fetch(`${API_URL}${path}`, opts)
  return resp.json()
}

async function cleanDb() {
  // Direct DB clean via API — delete all project data
  // We create fresh projects each cycle, old ones accumulate but don't interfere
}

interface CycleResult {
  cycle: number
  status: string // OK | NO_TASKS | NO_RESPONSE | NO_DONE | NO_FILES | ERROR
  phases: number
  tasks: number
  doneTasks: number
  duplicates: number
  selfAssigns: number
  files: number
  screens: number
  stopWorked: boolean
  restartWorked: boolean
  agents: string[]
  assistantMessages: number
  elapsedMs: number
}

async function waitForDoneAndFiles(
  projectId: string,
  timeoutMs: number,
): Promise<{ doneTasks: number; tasks: any[]; files: number; screens: number }> {
  const start = Date.now()
  let lastResult = { doneTasks: 0, tasks: [] as any[], files: 0, screens: 0 }
  while (Date.now() - start < timeoutMs) {
    try {
      const plan = await apiCall('GET', `/api/v1/projects/${projectId}/plan-tree`)
      const tasks = (plan.phases || []).flatMap((p: any) => p.tasks || [])
      const doneTasks = tasks.filter((t: any) => t.status === 'done').length

      const filesResp = await apiCall('GET', `/api/v1/projects/${projectId}/files`).catch(() => ({}))
      const filesList = Array.isArray(filesResp) ? filesResp : filesResp.files || []
      const screensResp = await apiCall('GET', `/api/v1/projects/${projectId}/design/screens`).catch(() => ({}))
      const screensList = Array.isArray(screensResp) ? screensResp : screensResp.screens || []

      lastResult = {
        doneTasks,
        tasks,
        files: filesList.length,
        screens: screensList.length,
      }

      // Success: at least 1 done task AND (1 file OR 1 screen)
      if (doneTasks >= 1 && (filesList.length >= 1 || screensList.length >= 1)) {
        return lastResult
      }
    } catch { /* ignore */ }
    await new Promise((r) => setTimeout(r, 15_000))
  }
  return lastResult
}

async function waitForTasks(
  projectId: string,
  minTasks: number,
  timeoutMs: number,
): Promise<{ phases: any[]; tasks: any[] }> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
      const plan = await apiCall('GET', `/api/v1/projects/${projectId}/plan-tree`)
      const phases = plan.phases || []
      const tasks = phases.flatMap((p: any) => p.tasks || [])
      if (tasks.length >= minTasks) return { phases, tasks }
    } catch { /* API may be down during recovery */ }
    await new Promise((r) => setTimeout(r, 10_000))
  }
  const plan = await apiCall('GET', `/api/v1/projects/${projectId}/plan-tree`).catch(() => ({ phases: [] }))
  return { phases: plan.phases || [], tasks: (plan.phases || []).flatMap((p: any) => p.tasks || []) }
}

test.describe('Long-term UI stability test', () => {
  test.setTimeout(MAX_CYCLES * 20 * 60 * 1000) // ~20min per cycle max

  test('repeated cycles via UI', async ({ page }) => {
    const results: CycleResult[] = []

    for (let cycle = 1; cycle <= MAX_CYCLES; cycle++) {
      const cycleStart = Date.now()
      console.log(`\n[longterm-ui] ═══ CYCLE ${cycle}/${MAX_CYCLES} START ═══`)

      let result: CycleResult = {
        cycle,
        status: 'FAIL',
        phases: 0,
        tasks: 0,
        doneTasks: 0,
        duplicates: 0,
        selfAssigns: 0,
        files: 0,
        screens: 0,
        stopWorked: false,
        restartWorked: false,
        agents: [],
        assistantMessages: 0,
        elapsedMs: 0,
      }

      try {
        // ── 0. Wait for backend to be reachable (infra-watchdog recovers colima) ──
        const infraStart = Date.now()
        while (Date.now() - infraStart < 180_000) {
          try {
            const r = await fetch(`${API_URL}/health`, { signal: AbortSignal.timeout(5000) })
            if (r.ok) break
          } catch { /* infra-watchdog will recover */ }
          console.log(`[longterm-ui] Cycle ${cycle}: waiting for backend...`)
          await new Promise((r) => setTimeout(r, 10_000))
        }

        // ── 1. Setup auth + navigate to dashboard ──
        await setupAuth(page)
        await page.goto(`${FE_URL}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 15_000 })

        // ── 2. Create project via API (faster than UI form) ──
        const projectName = `lt-ui-${cycle}-${Date.now().toString(36)}`
        const project = await apiCall('POST', '/api/v1/projects', {
          name: projectName,
          description: `Longterm UI cycle ${cycle}`,
          workspace_type: 'server_managed',
        })
        const projectId = project.id
        if (!projectId) {
          console.log(`[longterm-ui] Cycle ${cycle}: project creation failed`)
          result.status = 'PROJECT_FAIL'
          results.push(result)
          continue
        }
        console.log(`[longterm-ui] Project: ${projectId} (${projectName})`)

        // ── 3. Navigate to project page ──
        await page.goto(`${FE_URL}/projects/${projectId}`, {
          waitUntil: 'domcontentloaded',
          timeout: 15_000,
        })
        await page.waitForTimeout(2000)

        // ── 4. Send chat via UI ──
        const chatBox = page.getByRole('textbox').last()
        await chatBox.waitFor({ state: 'visible', timeout: 10_000 })
        await chatBox.fill('할 일 관리 웹앱 만들고 싶어')
        await chatBox.press('Enter')
        console.log(`[longterm-ui] Chat sent`)

        // ── 5. Verify user message appears in UI ──
        await expect(page.getByText('할 일 관리 웹앱').first()).toBeVisible({ timeout: 10_000 })

        // ── 6. Wait for tasks to appear (poll API, watch UI) ──
        console.log(`[longterm-ui] Waiting for tasks...`)
        const { phases, tasks } = await waitForTasks(projectId, 3, TASK_WAIT_MS)
        result.phases = phases.length
        result.tasks = tasks.length
        console.log(`[longterm-ui] phases:${result.phases} tasks:${result.tasks}`)

        if (tasks.length === 0) {
          result.status = 'NO_TASKS'
          result.elapsedMs = Date.now() - cycleStart
          results.push(result)
          console.log(`[longterm-ui] Cycle ${cycle}: NO_TASKS`)
          continue
        }

        // ── 7. Check duplicates ──
        const titleCounts: Record<string, number> = {}
        for (const t of tasks) {
          const norm = (t.title || '').trim().toLowerCase()
          titleCounts[norm] = (titleCounts[norm] || 0) + 1
        }
        result.duplicates = Object.values(titleCounts).filter((c) => c > 1).length
        if (result.duplicates > 0) {
          console.warn(`[longterm-ui] DUPLICATES: ${JSON.stringify(Object.entries(titleCounts).filter(([, c]) => c > 1))}`)
        }

        // ── 8. Check self-assigns ──
        for (const t of tasks) {
          const creator = (t.agent_name || '').toLowerCase()
          const assigned = (t.assigned_agent_name || '').toLowerCase()
          if (creator && assigned && creator === assigned) result.selfAssigns++
        }

        // ── 9. Collect agents ──
        const chat = await apiCall('GET', `/api/v1/projects/${projectId}/chat`).catch(() => ({ messages: [] }))
        result.agents = [
          ...new Set(
            (chat.messages || [])
              .filter((m: any) => m.role === 'assistant' && m.agent_name)
              .map((m: any) => m.agent_name),
          ),
        ] as string[]

        // ── 10. Test stop-all via UI ──
        const stopBtn = page.getByRole('button', { name: /중지/ })
        if (await stopBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
          await stopBtn.click()
          await page.waitForTimeout(3000)
          // Verify tasks transitioned (check API)
          const afterStop = await apiCall('GET', `/api/v1/projects/${projectId}/plan-tree`).catch(() => ({ phases: [] }))
          const blockedTasks = (afterStop.phases || [])
            .flatMap((p: any) => p.tasks || [])
            .filter((t: any) => t.status === 'blocked')
          result.stopWorked = blockedTasks.length > 0
          console.log(`[longterm-ui] Stop: ${result.stopWorked ? 'OK' : 'FAIL'} (${blockedTasks.length} blocked)`)
        }

        // ── 11. Test restart via UI ──
        await page.waitForTimeout(2000)
        const restartBtn = page.getByRole('button', { name: /재시작/ })
        if (await restartBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
          await restartBtn.click()
          await page.waitForTimeout(3000)
          const afterRestart = await apiCall('GET', `/api/v1/projects/${projectId}/plan-tree`).catch(() => ({ phases: [] }))
          const pendingTasks = (afterRestart.phases || [])
            .flatMap((p: any) => p.tasks || [])
            .filter((t: any) => t.status === 'pending')
          result.restartWorked = pendingTasks.length > 0
          console.log(`[longterm-ui] Restart: ${result.restartWorked ? 'OK' : 'FAIL'} (${pendingTasks.length} pending)`)
        }

        // ── 12. Wait for delegation chain to complete (done tasks + files) ──
        console.log(`[longterm-ui] Waiting up to ${Math.round(DONE_WAIT_MS / 60000)}m for done tasks + files...`)
        const completion = await waitForDoneAndFiles(projectId, DONE_WAIT_MS)
        result.doneTasks = completion.doneTasks
        result.files = completion.files
        result.screens = completion.screens

        // ── 13. Recheck assistant responses ──
        const finalChat = await apiCall('GET', `/api/v1/projects/${projectId}/chat`).catch(() => ({ messages: [] }))
        const assistantMsgs = (finalChat.messages || []).filter(
          (m: any) => m.role === 'assistant' && (m.content?.trim() || (m.actions || []).length > 0),
        )
        result.assistantMessages = assistantMsgs.length

        // ── 14. Take screenshot ──
        await page.reload({ waitUntil: 'domcontentloaded', timeout: 15_000 }).catch(() => {})
        await page.waitForTimeout(2000)
        await page.screenshot({
          path: `test-results/longterm-ui-cycle-${cycle}.png`,
          fullPage: true,
        }).catch(() => {})

        // ── 15. Strict success judgement ──
        if (result.assistantMessages === 0) {
          result.status = 'NO_RESPONSE'
        } else if (result.doneTasks === 0) {
          result.status = 'NO_DONE'
        } else if (result.files === 0 && result.screens === 0) {
          result.status = 'NO_FILES'
        } else {
          result.status = 'OK'
        }
      } catch (err) {
        console.error(`[longterm-ui] Cycle ${cycle} error:`, (err as Error).message)
        result.status = 'ERROR'
      }

      result.elapsedMs = Date.now() - cycleStart
      results.push(result)

      // ── Log cycle result ──
      console.log(
        `[longterm-ui] ═══ CYCLE ${cycle} END: ` +
          `${result.status} tasks=${result.tasks} done=${result.doneTasks} ` +
          `dups=${result.duplicates} self=${result.selfAssigns} ` +
          `stop=${result.stopWorked} restart=${result.restartWorked} ` +
          `files=${result.files} screens=${result.screens} ` +
          `msgs=${result.assistantMessages} agents=[${result.agents.join(',')}] ` +
          `${Math.round(result.elapsedMs / 1000)}s ═══`,
      )

      // Brief pause between cycles
      await new Promise((r) => setTimeout(r, 30_000))
    }

    // ── Final summary ──
    const total = results.length
    const ok = results.filter((r) => r.status === 'OK').length
    const noResponse = results.filter((r) => r.status === 'NO_RESPONSE').length
    const noTasks = results.filter((r) => r.status === 'NO_TASKS').length
    const noDone = results.filter((r) => r.status === 'NO_DONE').length
    const noFiles = results.filter((r) => r.status === 'NO_FILES').length
    const totalDups = results.reduce((s, r) => s + r.duplicates, 0)
    const totalSelf = results.reduce((s, r) => s + r.selfAssigns, 0)
    const totalTasks = results.reduce((s, r) => s + r.tasks, 0)
    const totalDone = results.reduce((s, r) => s + r.doneTasks, 0)
    const totalFiles = results.reduce((s, r) => s + r.files, 0)
    const totalScreens = results.reduce((s, r) => s + r.screens, 0)

    console.log('\n[longterm-ui] ════════════════════════════')
    console.log('[longterm-ui] LONG-TERM UI TEST COMPLETE')
    console.log('[longterm-ui] ════════════════════════════')
    console.log(`[longterm-ui] Cycles: ${total}`)
    console.log(`[longterm-ui]   OK (done+files): ${ok}`)
    console.log(`[longterm-ui]   NO_RESPONSE:     ${noResponse}`)
    console.log(`[longterm-ui]   NO_TASKS:        ${noTasks}`)
    console.log(`[longterm-ui]   NO_DONE:         ${noDone}`)
    console.log(`[longterm-ui]   NO_FILES:        ${noFiles}`)
    console.log(`[longterm-ui] Total tasks: ${totalTasks} (done: ${totalDone})`)
    console.log(`[longterm-ui] Total files: ${totalFiles} (screens: ${totalScreens})`)
    console.log(`[longterm-ui] Duplicates: ${totalDups} | Self-assigns: ${totalSelf}`)

    // Invariants — no duplicate or self-assign tasks ever
    expect(totalDups, 'Duplicate tasks detected across cycles').toBe(0)
    expect(totalSelf, 'Self-assign tasks detected across cycles').toBe(0)
    // At least one cycle must reach full completion (done task + file/screen)
    expect(ok, 'No cycle completed end-to-end (done task + file/screen produced)').toBeGreaterThanOrEqual(1)
    // Total done tasks across all cycles — delegation chain must actually execute
    expect(totalDone, 'No tasks completed across entire run').toBeGreaterThanOrEqual(1)
  })
})
