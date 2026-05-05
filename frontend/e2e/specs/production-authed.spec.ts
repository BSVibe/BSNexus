import { test, expect } from '@playwright/test'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

/**
 * Production smoke — Stage 3 (authenticated CRUD on real backend).
 *
 * Reuses ``e2e/.auth/prod-test-user.json`` if present; the entire
 * suite skips otherwise so ``pnpm exec playwright test`` doesn't fail
 * for users who haven't provisioned a storage state yet.
 *
 * To populate the storage state once:
 *
 * ```sh
 * cd frontend
 * mkdir -p e2e/.auth
 * pnpm exec playwright codegen \
 *   --save-storage e2e/.auth/prod-test-user.json \
 *   https://nexus.bsvibe.dev/dashboard
 * # Sign in via the BSVibe SSO flow, close the codegen window.
 * ```
 *
 * The storage-state path is gitignored via ``frontend/.gitignore``
 * (``e2e/.auth/`` rule below — added in this commit).
 *
 * Stage 4 (golden-path with real LLM dispatch) layers on top of this
 * — same auth source, additional spec, separate user-OK gate.
 */

const STORAGE = resolve(__dirname, '..', '.auth', 'prod-test-user.json')
const FE = 'https://nexus.bsvibe.dev'
const SHOTS = resolve(__dirname, '..', '..', 'test-results', 'prod-authed')

const skipReason =
  `Storage state at ${STORAGE} not found. ` +
  `Run e2e/specs/_populate-prod-storage.spec.ts with ` +
  `BSVIBE_TEST_EMAIL/BSVIBE_TEST_PASSWORD set to populate.`

async function shot(page: import('@playwright/test').Page, name: string) {
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

test.describe('Production smoke — authenticated CRUD', () => {
  test.skip(!existsSync(STORAGE), skipReason)
  test.use({ storageState: STORAGE, baseURL: FE })

  test('authed dashboard renders the founder shell', async ({ page }) => {
    await page.goto('/dashboard')
    // The chat textarea is the canonical post-auth element — present
    // on every founder-UX route.
    await expect(page.locator('textarea').first()).toBeVisible({ timeout: 15_000 })
    await shot(page, '01-dashboard')
  })

  test('Settings → Executors panel reachable', async ({ page }) => {
    await page.goto('/settings')
    // /settings opens on the Integrations tab by default. Click the
    // Executors section in the left rail. The user account's UI is in
    // Korean (실행기 / Register executor → 실행기 등록), so match either.
    await expect(page.locator('main')).toBeVisible({ timeout: 15_000 })
    await shot(page, '02-settings-landing')

    const executorsTab = page
      .getByRole('button', { name: /Executors|실행기/i })
      .or(page.getByRole('link', { name: /Executors|실행기/i }))
      .first()
    await executorsTab.click({ timeout: 10_000 })

    await expect(
      page.getByRole('button', { name: /Register executor|실행기 등록/i }).first(),
    ).toBeVisible({ timeout: 15_000 })
    await shot(page, '02-settings-executors-visible')
  })

  test('create + delete a temporary llm_api executor config', async ({
    page,
    request,
  }) => {
    // Pull the BSVibe access token out of the live session — the API
    // requires ``Authorization: Bearer <jwt>`` and we drive CRUD via
    // request.* rather than UI clicks for tighter assertions.
    // BSNexus's useAuth stashes the token under ``bsnexus_access_token``
    // (see frontend/src/hooks/useAuth.ts:51).
    await page.goto('/dashboard')
    await shot(page, '03-dashboard-before-crud')
    const token = await page.evaluate(
      () => localStorage.getItem('bsnexus_access_token'),
    )
    expect(token, 'access token missing in storage state').toBeTruthy()

    const ts = Date.now()
    const auth = { Authorization: `Bearer ${token}` }

    // Create
    const create = await request.post(`${FE}/api/v1/executor-configs`, {
      headers: auth,
      data: {
        name: `e2e-prod-temp-${ts}`,
        executor_type: 'llm_api',
        config: { model: 'anthropic/claude-3-5-sonnet' },
        api_key: 'sk-prod-e2e-test-not-real',
        is_selected: false,
      },
    })
    expect(create.status(), `create failed: ${await create.text()}`).toBe(201)
    const created = await create.json()
    expect(created.executor_type).toBe('llm_api')
    expect(created.has_api_key).toBe(true)
    expect(created.config).not.toHaveProperty('api_key')

    try {
      // Confirm in list
      const list = await request.get(`${FE}/api/v1/executor-configs`, {
        headers: auth,
      })
      expect(list.status()).toBe(200)
      const rows: Array<{ id: string }> = await list.json()
      expect(rows.find((r) => r.id === created.id)).toBeDefined()
    } finally {
      // Always delete — best-effort even if list assertions failed.
      const del = await request.delete(
        `${FE}/api/v1/executor-configs/${created.id}`,
        { headers: auth },
      )
      expect(del.status()).toBe(204)
    }

    // Confirm gone
    const refetch = await request.get(
      `${FE}/api/v1/executor-configs/${created.id}`,
      { headers: auth },
    )
    expect(refetch.status()).toBe(404)
  })
})
