import { test, expect } from '@playwright/test'

import { setupPage } from '../helpers/mock-api'

// Visual evidence dir — gitignored via ``frontend/.gitignore``
// (``test-results/`` rule). Lets reviewers eyeball the type-aware
// modal flow without re-running the suite. Not committed.
const SHOT_DIR = 'test-results/phase-2-evidence'

/**
 * Phase 2 dispatch-paths e2e — taxonomy collapse + DirectLLMAdapter wire.
 *
 * Pin three Settings-page contracts that the user-facing UX depends on:
 *
 *  1. The modal type selector exposes exactly two values (``bsgateway``,
 *     ``llm_api``) with the legacy CLI taxonomy
 *     (``claude_code`` / ``codex`` / ``opencode`` / ``worker``) gone.
 *  2. Switching the type re-renders the field set: bsgateway carries
 *     gateway URL + gateway API key + model; llm_api carries
 *     model + api_key + base_url.
 *  3. Both paths reach POST /api/v1/executor-configs successfully —
 *     server is responsible for the actual dispatch (BSGateway worker
 *     pool vs litellm tool loop), but the wire from the modal is
 *     identical.
 *
 * Skipped on mobile viewports — Settings is a desktop surface.
 */

test.describe('Settings — Executor type taxonomy (Phase 2)', () => {
  test.beforeEach(async ({ page, viewport }) => {
    test.skip(
      (viewport?.width ?? 0) < 800,
      'Settings page is desktop-only in v1; mobile drawer covers chat only.',
    )
    // Pin English locale — the user's local devcontainer defaults to
    // Korean. ``LOCALE_STORAGE_KEY`` is read in ``IntlProvider`` on
    // mount, so addInitScript writes it before the page loads.
    await page.addInitScript(() => {
      localStorage.setItem('bsnexus.locale', 'en')
    })
    // Disable all CSS animations/transitions for stable screenshots,
    // and zero out ``.cmd-mask``'s ``backdrop-filter: blur(4px)``
    // — backdrop-filter is unreliable in offscreen rendering and
    // makes the underlying page look murky in fullPage shots.
    await page.addInitScript(() => {
      const s = document.createElement('style')
      s.textContent = `
        *, *::before, *::after { transition: none !important; animation: none !important; }
        .cmd-mask { backdrop-filter: none !important; }
      `
      document.head.appendChild(s)
    })
    await setupPage(page, '/settings')

    // Settings is tabbed (Integrations / Executors / Language); the
    // landing tab is Integrations. The Executors panel is what we're
    // testing — click in.
    await page.getByRole('button', { name: 'Executors' }).click()
    await expect(page.getByRole('button', { name: /Register executor/i })).toBeVisible()
  })

  test('modal type selector exposes only bsgateway + llm_api', async ({ page }) => {
    await page.getByRole('button', { name: /Register executor/i }).click()

    const select = page.locator('select').first()
    await expect(select).toBeVisible()
    const optionValues = await select.locator('option').evaluateAll((opts) =>
      opts.map((o) => (o as HTMLOptionElement).value),
    )
    expect(optionValues.sort()).toEqual(['bsgateway', 'llm_api'])
  })

  test('default type is bsgateway and shows BSVibe-infra fields', async ({ page }) => {
    await page.getByRole('button', { name: /Register executor/i }).click()
    const select = page.locator('select').first()
    await expect(select).toHaveValue('bsgateway')

    // bsgateway carries: gateway URL, gateway API key, model.
    await expect(page.getByPlaceholder(/gateway\.bsvibe\.dev/i)).toBeVisible()
    await expect(page.getByPlaceholder(/^bsg-/i)).toBeVisible()
    await expect(page.getByPlaceholder(/openai\/gpt-4o.*claude/i)).toBeVisible()

    await page.locator('.cmd').screenshot({ path: `${SHOT_DIR}/01-modal-default-bsgateway.png` })
  })

  test('switching to llm_api renders direct-LLM fields', async ({ page }) => {
    await page.getByRole('button', { name: /Register executor/i }).click()
    await page.locator('select').first().selectOption('llm_api')

    // llm_api carries: model, api_key, base_url. Gateway-specific
    // placeholders must be gone — they're tied to the bsgateway shape.
    await expect(page.getByPlaceholder(/openai\/gpt-4o.*claude/i)).toBeVisible()
    await expect(page.getByPlaceholder(/gateway\.bsvibe\.dev/i)).toHaveCount(0)
    await expect(page.getByPlaceholder(/^bsg-/i)).toHaveCount(0)

    await page.locator('.cmd').screenshot({ path: `${SHOT_DIR}/02-modal-generic-llm.png` })
  })

  test('switching back from llm_api to bsgateway restores gateway fields', async ({
    page,
  }) => {
    await page.getByRole('button', { name: /Register executor/i }).click()
    const select = page.locator('select').first()
    await select.selectOption('llm_api')
    await select.selectOption('bsgateway')
    await expect(page.getByPlaceholder(/gateway\.bsvibe\.dev/i)).toBeVisible()
    await expect(page.getByPlaceholder(/^bsg-/i)).toBeVisible()
  })

  test('Register button stays disabled until name is filled', async ({ page }) => {
    await page.getByRole('button', { name: /Register executor/i }).click()
    const registerBtn = page.getByRole('button', { name: 'Register', exact: true })
    await expect(registerBtn).toBeDisabled()
    await page.getByPlaceholder(/GPT-4o Production/i).fill('Test BSGW')
    await expect(registerBtn).toBeEnabled()
  })

  test('creating a llm_api config posts to /executor-configs', async ({ page }) => {
    // Capture POST body — the dispatcher branches on ``executor_type``,
    // so this is the one wire-level fact we need to pin from the UI.
    let posted: Record<string, unknown> | null = null
    await page.route('**/api/v1/executor-configs', (route) => {
      if (route.request().method() === 'POST') {
        posted = route.request().postDataJSON() as Record<string, unknown>
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'exec-new',
            tenant_id: '00000000-0000-0000-0000-000000000000',
            name: posted!.name,
            executor_type: posted!.executor_type,
            config: posted!.config ?? {},
            description: null,
            is_selected: false,
            has_api_key: false,
            created_at: '2026-05-04T00:00:00Z',
            updated_at: '2026-05-04T00:00:00Z',
          }),
        })
      }
      // GET stays on the default fixture
      return route.continue()
    })

    await page.getByRole('button', { name: /Register executor/i }).click()
    await page.locator('select').first().selectOption('llm_api')
    await page.getByPlaceholder(/GPT-4o Production/i).fill('Direct claude')
    await page
      .getByPlaceholder(/openai\/gpt-4o.*claude/i)
      .fill('anthropic/claude-3-5-sonnet')
    await page.getByRole('button', { name: 'Register', exact: true }).click()

    await expect.poll(() => posted).not.toBeNull()
    expect(posted!.executor_type).toBe('llm_api')
    expect((posted!.config as Record<string, unknown>).model).toBe(
      'anthropic/claude-3-5-sonnet',
    )
    // ``api_key`` plaintext stays in the request payload (encrypted
    // server-side) but the response shape carries ``has_api_key`` only.
    expect(posted!).not.toHaveProperty('api_key_encrypted')

    // 03 is the post-submit page state — modal already closed, so
    // capture the full page to show the new card landing in the list.
    await page.screenshot({
      path: `${SHOT_DIR}/03-generic-llm-submitted.png`,
      fullPage: true,
    })
  })

  test('mock fixture renders both type cards', async ({ page }) => {
    // mockExecutorConfigs has one bsgateway + one llm_api; both
    // should appear in the registered list. This guards the fixture
    // schema match (post-Phase-2a ``executor_type`` values + the
    // ``is_selected`` field name).
    await expect(page.getByText('BSGateway Prod', { exact: true })).toBeVisible()
    await expect(page.getByText('Direct Claude (no BSVibe)', { exact: true })).toBeVisible()

    await page.screenshot({
      path: `${SHOT_DIR}/04-list-both-types.png`,
      fullPage: true,
    })
  })
})
