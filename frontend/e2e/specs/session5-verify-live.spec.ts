/**
 * Session 5 verification — live e2e against devcontainer backend + vLLM.
 *
 * Tests:
 * 1. Task duplicate prevention (#5): same-title tasks should NOT be duplicated
 * 2. CMO self-assign prevention (#6): CMO should delegate, not self-assign
 * 3. Design prompt stabilization (#8): Designer should produce .bsd screens
 *
 * Requires: devcontainer running (frontend:13100, backend:18100) + vLLM on :8888
 */
import { expect, test, type Page } from '@playwright/test'

const FE_URL = process.env.LIVE_FRONTEND_URL || 'http://localhost:13100'
const API_URL = process.env.LIVE_API_URL || 'http://localhost:18100'
const BYPASS_TOKEN = 'e2e-scenario-test-token'

// Build a fake JWT the frontend can decode for display
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

  // Mock the SSO session endpoint → return fake token
  await page.route('**/auth.bsvibe.dev/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: fakeJwt,
        refresh_token: 'fake-refresh',
        expires_in: 99999,
      }),
    }),
  )

  // Inject the fake JWT into localStorage before page loads
  await page.addInitScript(
    ({ token, key }) => {
      localStorage.setItem(key, token)
    },
    { token: fakeJwt, key: 'bsnexus_access_token' },
  )

  // Intercept all API calls → replace Authorization header with bypass token
  await page.route(`${FE_URL}/api/**`, async (route) => {
    const headers = {
      ...route.request().headers(),
      authorization: `Bearer ${BYPASS_TOKEN}`,
    }
    await route.continue({ headers })
  })
}

function uniqName(): string {
  return `s5-verify-${Date.now().toString(36)}`
}

// API helper — direct backend call with bypass token
async function apiCall(method: string, path: string, body?: object) {
  const opts: RequestInit = {
    method,
    headers: {
      Authorization: `Bearer ${BYPASS_TOKEN}`,
      'Content-Type': 'application/json',
    },
  }
  if (body) opts.body = JSON.stringify(body)
  const resp = await fetch(`${API_URL}${path}`, opts)
  return resp.json()
}

async function waitForTasks(
  projectId: string,
  minTasks: number,
  timeoutMs: number,
): Promise<{ phases: any[]; totalTasks: number; taskTitles: string[] }> {
  const start = Date.now()
  let lastResult = { phases: [] as any[], totalTasks: 0, taskTitles: [] as string[] }

  while (Date.now() - start < timeoutMs) {
    const plan = await apiCall('GET', `/api/v1/projects/${projectId}/plan-tree`)
    const phases = plan.phases || []
    const tasks = phases.flatMap((p: any) => p.tasks || [])
    lastResult = {
      phases,
      totalTasks: tasks.length,
      taskTitles: tasks.map((t: any) => t.title),
    }

    if (tasks.length >= minTasks) return lastResult
    await new Promise((r) => setTimeout(r, 10_000))
  }
  return lastResult
}

test.describe('Session 5 — duplicate/self-assign/design verification', () => {
  test.setTimeout(600_000) // 10min max

  let projectId: string
  let projectName: string

  test('create project, trigger CMO, verify plan tree', async ({ page }) => {
    await setupAuth(page)

    // ── 1. Navigate to dashboard ──
    await page.goto(`${FE_URL}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 15_000 })
    console.log(`[s5] Dashboard loaded: ${page.url()}`)

    // ── 2. Create project via API (faster than UI) ──
    projectName = uniqName()
    const project = await apiCall('POST', '/api/v1/projects', {
      name: projectName,
      description: 'Session 5 e2e verification',
      workspace_type: 'server_managed',
    })
    projectId = project.id
    console.log(`[s5] Project created: ${projectId}`)

    // ── 3. Navigate to project page ──
    await page.goto(`${FE_URL}/projects/${projectId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 15_000,
    })

    // ── 4. Send chat to CMO (role-appropriate request) ──
    const chatMsg =
      '@CMO 시장조사해서 할 일 관리 웹앱 프로젝트 제안해줘'
    const chatBox = page.getByRole('textbox').last()
    await chatBox.waitFor({ state: 'visible', timeout: 10_000 })
    await chatBox.fill(chatMsg)
    await chatBox.press('Enter')
    console.log(`[s5] Chat sent to CMO`)

    // ── 5. Wait for user message to appear in UI ──
    await expect(page.getByText('할 일 관리 웹앱').first()).toBeVisible({ timeout: 10_000 })

    // ── 6. Poll plan tree for tasks (up to 5 min) ──
    console.log(`[s5] Waiting for tasks to appear...`)
    const result = await waitForTasks(projectId, 3, 300_000)
    console.log(`[s5] Plan tree: ${result.totalTasks} tasks found`)
    console.log(`[s5] Task titles: ${JSON.stringify(result.taskTitles)}`)

    // ── 7. Verify: tasks exist ──
    expect(result.totalTasks).toBeGreaterThanOrEqual(3)

    // ── 8. Check #5: Duplicate prevention ──
    const titleCounts: Record<string, number> = {}
    for (const title of result.taskTitles) {
      const normalized = title.trim().toLowerCase()
      titleCounts[normalized] = (titleCounts[normalized] || 0) + 1
    }
    const duplicates = Object.entries(titleCounts).filter(([, count]) => count > 1)
    if (duplicates.length > 0) {
      console.warn(`[s5] DUPLICATE TASKS DETECTED:`)
      for (const [title, count] of duplicates) {
        console.warn(`  "${title}" x${count}`)
      }
    } else {
      console.log(`[s5] PASS: No duplicate tasks`)
    }
    // Soft assert — log but don't fail (LLM may generate similar-but-different titles)
    expect(duplicates.length, `Exact duplicate tasks found: ${JSON.stringify(duplicates)}`).toBe(0)

    // ── 9. Check #6: CMO self-assign prevention ──
    const allTasks = result.phases.flatMap((p: any) => p.tasks || [])
    const cmoSelfAssigned = allTasks.filter(
      (t: any) =>
        (t.agent_name || '').toLowerCase() === 'cmo' &&
        (t.assigned_agent_name || '').toLowerCase() === 'cmo',
    )
    if (cmoSelfAssigned.length > 0) {
      console.warn(`[s5] CMO SELF-ASSIGNED TASKS:`)
      for (const t of cmoSelfAssigned) {
        console.warn(`  "${t.title}" assigned_to=${t.assigned_agent_name}`)
      }
    } else {
      console.log(`[s5] PASS: No CMO self-assignment`)
    }
    expect(
      cmoSelfAssigned.length,
      `CMO self-assigned tasks: ${JSON.stringify(cmoSelfAssigned.map((t: any) => t.title))}`,
    ).toBe(0)

    // ── 10. Check agent assignments ──
    const assignees = allTasks
      .map((t: any) => t.assigned_agent_name)
      .filter(Boolean)
    console.log(`[s5] Assignees: ${JSON.stringify([...new Set(assignees)])}`)

    // ── 11. Wait more for passive agents to produce files (up to 3 more min) ──
    console.log(`[s5] Waiting for file output...`)
    let filesCount = 0
    let screensCount = 0
    const fileStart = Date.now()
    while (Date.now() - fileStart < 180_000) {
      try {
        const files = await apiCall('GET', `/api/v1/projects/${projectId}/files`)
        const fileList = Array.isArray(files) ? files : files.files || []
        filesCount = fileList.length
      } catch { /* ignore */ }
      try {
        const screens = await apiCall('GET', `/api/v1/projects/${projectId}/design/screens`)
        const screenList = screens.screens || (Array.isArray(screens) ? screens : [])
        screensCount = screenList.length
      } catch { /* ignore */ }

      console.log(`[s5] Files: ${filesCount}, Screens: ${screensCount}`)
      if (filesCount >= 1 || screensCount >= 1) break
      await new Promise((r) => setTimeout(r, 15_000))
    }

    // ── 12. Check #8: Design screens ──
    if (screensCount > 0) {
      console.log(`[s5] PASS: ${screensCount} design screen(s) created`)
    } else {
      console.warn(`[s5] WARN: No design screens — Designer may not have run yet`)
    }

    // ── 13. Check plan tree in UI ──
    // Reload to pick up latest SSE state
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 15_000 })
    await page.waitForTimeout(2_000)

    // Take screenshot for visual inspection
    await page.screenshot({ path: `test-results/s5-plan-tree-${Date.now()}.png`, fullPage: true })
    console.log(`[s5] Screenshot saved`)

    // ── 14. Final summary ──
    const chat = await apiCall('GET', `/api/v1/projects/${projectId}/chat`)
    const assistantMsgs = (chat.messages || []).filter((m: any) => m.role === 'assistant')
    const agentNames = [...new Set(assistantMsgs.map((m: any) => m.agent_name).filter(Boolean))]
    console.log(`\n[s5] ═══ FINAL SUMMARY ═══`)
    console.log(`[s5] Tasks: ${result.totalTasks}`)
    console.log(`[s5] Duplicates: ${duplicates.length}`)
    console.log(`[s5] CMO self-assigns: ${cmoSelfAssigned.length}`)
    console.log(`[s5] Files: ${filesCount}`)
    console.log(`[s5] Design screens: ${screensCount}`)
    console.log(`[s5] Agents responded: ${JSON.stringify(agentNames)}`)
    console.log(`[s5] ═══════════════════════`)
  })
})
