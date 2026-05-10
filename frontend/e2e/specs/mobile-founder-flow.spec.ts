import { test, expect, type Page, type Route } from '@playwright/test'

import { injectAuth, blockSSORedirect, mockAllApis } from '../helpers/mock-api'

/**
 * Greenfield G7 — mobile-first founder flow.
 *
 * Acceptance: founder can complete Direct / Decide / Review entirely from
 * a phone viewport (iPhone 13 / Pixel 5 projects). Specifically:
 *   - Dashboard exposes a Direction input card that hits the new
 *     ``POST /api/v1/directions`` endpoint (G1) — not the legacy
 *     ``/api/v1/messages`` chat pipeline.
 *   - Decisions view buttons meet the 44px touch-target floor.
 *   - Brief view renders the 5 sections without horizontal overflow.
 *
 * Source-of-truth: ``docs/e2e/g7-mobile-pwa-checklist.md`` +
 * ``~/Docs/BSNexus/planning/greenfield/rebuild-plan.md`` §G7.
 *
 * The mock founder helper covers the legacy nested-route shape used by
 * older specs; this file installs its own minimal flat-route mocks so it
 * stays focused on the greenfield surfaces.
 */

const PROJECT_ID = 'proj-g7-mobile'
const DIRECTION_ID = 'dir-001'
const REQUEST_ID = 'req-001'
const DECISION_ID = 'dec-001'
const DELIVERABLE_ID = 'del-001'

interface DirectionPostBody {
  body?: string
  source?: string
  target_hint?: string | null
  project_id?: string | null
}

interface CapturedPost {
  url: string
  body: DirectionPostBody | Record<string, unknown>
}

async function installFlatFounderMocks(page: Page, captured: CapturedPost[]): Promise<void> {
  const project = {
    id: PROJECT_ID,
    tenant_id: 'tenant-001',
    name: 'G7 Mobile Project',
    description: null,
    status: 'active',
    workspace_type: 'local_managed',
    github_repo_url: null,
    github_branch: null,
    repo_path: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }

  await page.route('**/api/v1/projects', (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([project]),
    })
  })
  await page.route(`**/api/v1/projects/${PROJECT_ID}`, (route: Route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(project) })
  })

  // POST /api/v1/directions — capture the body so the test can assert.
  await page.route(/\/api\/v1\/directions(\?|$)/, async (route: Route) => {
    const req = route.request()
    if (req.method() === 'POST') {
      const body = (req.postDataJSON?.() ?? {}) as DirectionPostBody
      captured.push({ url: req.url(), body })
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          direction: {
            id: DIRECTION_ID,
            project_id: PROJECT_ID,
            source: body.source ?? 'mobile_web',
            body: body.body ?? '',
            target_hint: body.target_hint ?? null,
            created_at: new Date().toISOString(),
          },
          request: {
            id: REQUEST_ID,
            tenant_id: 'tenant-001',
            project_id: PROJECT_ID,
            origin_message_id: null,
            intent_summary: body.body ?? '',
            status: 'open',
            originator_auth: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
          routing: null,
          acknowledgement: 'Direction accepted. A request has been opened.',
        }),
      })
    }
    return route.fulfill({ status: 405, body: 'method not allowed' })
  })

  // GET /api/v1/decisions — flat endpoint with project_id query.
  const decision = {
    id: DECISION_ID,
    tenant_id: 'tenant-001',
    project_id: PROJECT_ID,
    request_id: REQUEST_ID,
    origin_run_id: null,
    question: 'Database driver: PostgreSQL or SQLite for the smoke run?',
    options: ['PostgreSQL', 'SQLite'],
    blocking: true,
    resolution: null,
    resolved_by: null,
    resolved_at: null,
    created_at: new Date().toISOString(),
  }
  await page.route(/\/api\/v1\/decisions(\?|$)/, (route: Route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([decision]),
      })
    }
    return route.fulfill({ status: 405, body: '' })
  })
  await page.route(`**/api/v1/decisions/${DECISION_ID}/resolve`, async (route: Route) => {
    const body = (route.request().postDataJSON?.() ?? {}) as Record<string, unknown>
    captured.push({ url: route.request().url(), body })
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...decision,
        resolution: body.resolution ?? '',
        resolved_by: 'user-001',
        resolved_at: new Date().toISOString(),
      }),
    })
  })

  // GET /api/v1/deliverables — flat endpoint.
  const deliverable = {
    id: DELIVERABLE_ID,
    tenant_id: 'tenant-001',
    project_id: PROJECT_ID,
    request_id: REQUEST_ID,
    type: 'code',
    title: 'Add /healthz endpoint',
    summary: null,
    status: 'delivered',
    proof_state: 'verified',
    proof_summary: 'pytest passed (12 tests, 0 failures)',
    verifier_type: 'python_test',
    proof_command: null,
    proof_exit_code: 0,
    artifact_url: null,
    current_version_id: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }
  await page.route(/\/api\/v1\/deliverables(\?|$)/, (route: Route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([deliverable]),
      })
    }
    return route.fulfill({ status: 405, body: '' })
  })
  await page.route(`**/api/v1/deliverables/${DELIVERABLE_ID}/verify`, async (route: Route) => {
    captured.push({ url: route.request().url(), body: {} })
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(deliverable),
    })
  })

  // GET /api/v1/brief — typed 5-section payload (G7.1 wire shape).
  await page.route(/\/api\/v1\/brief(\?|$)/, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        scope: 'project',
        project_id: PROJECT_ID,
        sections: {
          shipped: [deliverable],
          needs_decision: [decision],
          blocked: [],
          running: [],
          next: [{ summary: 'Add /readyz endpoint after the smoke run.', request_id: null }],
        },
        generated_at: new Date().toISOString(),
      }),
    })
  })

  // GET /api/v1/requests
  await page.route(/\/api\/v1\/requests(\?|$)/, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  // GET /api/v1/runs
  await page.route(/\/api\/v1\/runs(\?|$)/, (route: Route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  })

  // GET /api/v1/messages — quiet legacy chat.
  await page.route(/\/api\/v1\/messages(\?|$)/, (route: Route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  })

  // SSE event stream — empty / connected.
  await page.route(/\/api\/v1\/events(\?|$)/, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'event: connected\ndata: {}\n\n',
    })
  })
}

async function suppressNextRuntimeOverlay(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const css = document.createElement('style')
    css.textContent =
      'nextjs-portal, [data-nextjs-toast], [data-nextjs-dialog-overlay] { display: none !important; }'
    document.head.appendChild(css)
  })
}

async function pinEnglishLocale(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('bsnexus.locale', 'en')
  })
}

test.describe('G7 mobile founder flow — Direct / Decide / Review', () => {
  let captured: CapturedPost[] = []

  test.beforeEach(async ({ page }, testInfo) => {
    if (testInfo.project.name === 'chromium') {
      testInfo.skip()
    }
    captured = []
    await blockSSORedirect(page)
    await injectAuth(page)
    await pinEnglishLocale(page)
    await suppressNextRuntimeOverlay(page)
    await mockAllApis(page)
    await installFlatFounderMocks(page, captured)
  })

  test('Direct — Direction input card on dashboard posts to /api/v1/directions', async ({ page }) => {
    await page.goto('/dashboard', { waitUntil: 'networkidle' })

    const card = page.getByTestId('direction-input-card')
    await expect(card).toBeVisible()

    const textarea = card.getByRole('textbox')
    await expect(textarea).toBeVisible()

    const submit = card.getByRole('button', { name: /direct|send|submit/i })
    await expect(submit).toBeVisible()

    // Empty body → submit is disabled (no API call).
    await expect(submit).toBeDisabled()

    await textarea.fill('Add a /healthz endpoint to the API')
    await expect(submit).toBeEnabled()

    await submit.click()

    // Endpoint must be the new flat directions route, NOT messages chat.
    await expect.poll(() => captured.filter((c) => c.url.includes('/api/v1/directions')).length).toBeGreaterThan(0)
    const post = captured.find((c) => c.url.includes('/api/v1/directions'))!
    expect((post.body as DirectionPostBody).body).toContain('/healthz')

    // Textarea clears after success.
    await expect(textarea).toHaveValue('')
  })

  test('Direct — submit button meets the 44px touch-target floor', async ({ page }) => {
    await page.goto('/dashboard', { waitUntil: 'networkidle' })

    const card = page.getByTestId('direction-input-card')
    await expect(card).toBeVisible()
    const submit = card.getByRole('button', { name: /direct|send|submit/i })
    const box = await submit.boundingBox()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  })

  test('Decide — option buttons on the Decisions tab meet the 44px touch-target floor', async ({ page }) => {
    await page.goto(`/projects/${PROJECT_ID}?tab=decisions`, { waitUntil: 'networkidle' })

    const optionButton = page.getByRole('button', { name: 'PostgreSQL' })
    await expect(optionButton).toBeVisible()
    const box = await optionButton.boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  })

  test('Decide — Decisions view has no horizontal overflow on iPhone 13 viewport', async ({ page }) => {
    await page.goto(`/projects/${PROJECT_ID}?tab=decisions`, { waitUntil: 'networkidle' })
    await expect(page.getByRole('button', { name: 'PostgreSQL' })).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(2)
  })

  test('Review — all 6 ProjectPage tabs are reachable on mobile without horizontal overflow', async ({ page }) => {
    await page.goto(`/projects/${PROJECT_ID}?tab=summary`, { waitUntil: 'networkidle' })

    // G7.5d — Brief 5-section vertical scroll became 6 top-level tabs.
    // Each tab name comes from `nexus.project.tab.*`; we assert by
    // clicking the Korean labels (devcontainer default locale).
    const tabs = ['지시', '요약', '의사결정', '납품', '진행', '막힘']
    for (const label of tabs) {
      await page.getByRole('button', { name: label }).first().click()
      await page.waitForTimeout(50)
    }

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(2)
  })

  test('Review — Re-verify button on the Deliverable card meets the 44px touch-target floor', async ({ page }) => {
    await page.goto(`/projects/${PROJECT_ID}?tab=shipped`, { waitUntil: 'networkidle' })

    const verifyBtn = page.getByRole('button', { name: /re-run verification/i }).first()
    await expect(verifyBtn).toBeVisible({ timeout: 10_000 })
    const box = await verifyBtn.boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  })

  test('Dashboard has no horizontal overflow on iPhone 13 viewport', async ({ page }) => {
    await page.goto('/dashboard', { waitUntil: 'networkidle' })
    await expect(page.getByTestId('direction-input-card')).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(2)
  })
})
