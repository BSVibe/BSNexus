import { test, expect } from '@playwright/test'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

/**
 * Production smoke — Stage 4 (golden-path with real LLM dispatch).
 *
 * Sends a real user message through the live BSNexus stack against
 * a tenant that has a *selected* ``llm_api`` executor configured to
 * point at the Mac Mini's ollama daemon over Tailscale. Watches the
 * resulting ExecutionRun until it transitions ``pending → running →
 * done`` and a deliverable lands.
 *
 * Skip gates (entire suite skips unless ALL satisfied):
 *
 *   1. ``e2e/.auth/prod-test-user.json`` exists (Stage 3 storageState)
 *   2. The signed-in tenant already has at least one *selected*
 *      ``llm_api`` executor with ``has_api_key=true`` —
 *      Stage 4 is gated on the founder having registered ollama (or
 *      equivalent) so we never spend tokens by accident.
 *
 * To prep on the live stack once:
 *
 *     # nexus.bsvibe.dev → Settings → 실행기 → "+ 실행기 등록"
 *     # Type: LLM API
 *     # Model: ollama/qwen3-coder:30b
 *     # API key: ollama          (any non-empty — litellm requires it)
 *     # Base URL: http://bsserver:11434
 *     # Selected: yes
 */

const STORAGE = resolve(__dirname, '..', '.auth', 'prod-test-user.json')
const FE = 'https://nexus.bsvibe.dev'
const SHOTS = resolve(__dirname, '..', '..', 'test-results', 'prod-golden-path')

const RUN_TIMEOUT_MS = 5 * 60_000 // 5 min — qwen3-coder:30b cold-start budget
const POLL_INTERVAL_MS = 2_000

const skipReason =
  `Storage state at ${STORAGE} not found OR no selected llm_api ` +
  `executor on the signed-in tenant. See _populate-prod-storage and ` +
  `Settings → 실행기 setup steps in the spec docstring.`

async function shot(page: import('@playwright/test').Page, name: string) {
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

test.describe('Production smoke — golden path with real LLM', () => {
  test.skip(!existsSync(STORAGE), skipReason)
  test.use({ storageState: STORAGE, baseURL: FE })

  test('user message → run dispatch → ollama → deliverable', async ({
    page,
    request,
  }) => {
    test.setTimeout(RUN_TIMEOUT_MS + 60_000)

    // ── Setup: pull the live JWT + verify the tenant has a selected
    //          llm_api executor. The Stage 4 gate is "founder has
    //          provisioned ollama on this account" — without it we
    //          skip rather than silently 0-charge cloud tokens.
    await page.goto('/dashboard')
    await expect(page.locator('textarea').first()).toBeVisible({ timeout: 15_000 })
    await shot(page, '01-dashboard-landing')

    const token = await page.evaluate(
      () => localStorage.getItem('bsnexus_access_token'),
    )
    expect(token, 'access token missing in storage state').toBeTruthy()
    const auth = { Authorization: `Bearer ${token}` }

    const execList = await request.get(`${FE}/api/v1/executor-configs`, {
      headers: auth,
    })
    expect(execList.status()).toBe(200)
    const executors: Array<{
      id: string
      executor_type: string
      is_selected: boolean
      has_api_key: boolean
      config: Record<string, unknown>
    }> = await execList.json()

    const selected = executors.find(
      (e) => e.is_selected && e.executor_type === 'llm_api' && e.has_api_key,
    )
    test.skip(
      !selected,
      `No selected llm_api executor with api_key on this tenant. ` +
        `Provision one (model=ollama/qwen3-coder:30b, base_url=http://bsserver:11434) ` +
        `before running Stage 4.`,
    )
    expect(selected!.config).toHaveProperty('model')
    expect(selected!.config).toHaveProperty('base_url')

    // ── Pick an existing project (any), or create a throwaway one.
    const projectsRes = await request.get(`${FE}/api/v1/projects`, {
      headers: auth,
    })
    expect(projectsRes.status()).toBe(200)
    const projects: Array<{ id: string; name: string }> = await projectsRes.json()
    let projectId: string
    let createdProject = false
    if (projects.length > 0) {
      projectId = projects[0].id
    } else {
      const created = await request.post(`${FE}/api/v1/projects`, {
        headers: auth,
        data: { name: `e2e-stage4-${Date.now()}`, description: 'Stage 4 throwaway' },
      })
      expect(created.status()).toBe(201)
      const body: { id: string } = await created.json()
      projectId = body.id
      createdProject = true
    }

    // ── Send the message. Keep the prompt small (qwen3-coder:30b
    //    streams ~50 tok/s on Apple Silicon, want < 2 min).
    const ts = Date.now()
    const userMessage = `Stage 4 golden-path probe ${ts}: reply with the single word PONG.`
    const sendRes = await request.post(
      `${FE}/api/v1/projects/${projectId}/messages`,
      {
        headers: auth,
        data: { content: userMessage },
      },
    )
    expect(sendRes.status(), `send failed: ${await sendRes.text()}`).toBe(201)

    // ── Watch the requests endpoint for the new Request the
    //    extractor created from our message, then watch its run for
    //    pending → running → done.
    const deadline = Date.now() + RUN_TIMEOUT_MS
    let requestId: string | null = null
    let runId: string | null = null
    let runState: string = 'unknown'

    while (Date.now() < deadline && runState !== 'done' && runState !== 'blocked') {
      const reqList = await request.get(
        `${FE}/api/v1/projects/${projectId}/requests`,
        { headers: auth },
      )
      if (reqList.status() === 200) {
        const reqs: Array<{ id: string; created_at: string }> = await reqList.json()
        // Most recent request, created at-or-after our message.
        const recent = reqs.find(
          (r) => new Date(r.created_at).getTime() >= ts - 5_000,
        )
        if (recent) {
          requestId = recent.id
          const runsRes = await request.get(
            `${FE}/api/v1/requests/${requestId}/runs`,
            { headers: auth },
          )
          if (runsRes.status() === 200) {
            const runs: Array<{ id: string; status: string }> = await runsRes.json()
            const run = runs[0]
            if (run) {
              runId = run.id
              runState = run.status
            }
          }
        }
      }
      if (runState === 'done' || runState === 'blocked') break
      await page.waitForTimeout(POLL_INTERVAL_MS)
    }

    expect(
      runState,
      `run timeout: state=${runState} request=${requestId} run=${runId}`,
    ).toBe('done')

    // ── Verify a deliverable was emitted. The Progress panel
    //    surfaces these — for the golden-path probe a single text
    //    artefact is enough; we just assert > 0.
    const delivRes = await request.get(
      `${FE}/api/v1/projects/${projectId}/deliverables`,
      { headers: auth },
    )
    expect(delivRes.status()).toBe(200)
    const deliverables: unknown[] = await delivRes.json()
    expect(deliverables.length).toBeGreaterThan(0)

    // Visual confirmation: navigate to the project, snap the
    // Progress / Inside surfaces.
    await page.goto(`/projects/${projectId}`)
    await page.waitForTimeout(500)
    await shot(page, '02-project-after-run')

    // ── Cleanup: drop the throwaway project so repeat runs don't
    //    accumulate. Existing-project runs leave artefacts behind on
    //    purpose for inspection.
    if (createdProject) {
      const del = await request.delete(`${FE}/api/v1/projects/${projectId}`, {
        headers: auth,
      })
      expect(del.status()).toBe(204)
    }
  })
})
