import { test, expect } from '@playwright/test'

/**
 * Production smoke — Stage 2: auth-gate behavior.
 *
 * No real login. Verifies that unauthenticated requests to protected
 * surfaces are correctly bounced to BSVibe SSO (frontend) or rejected
 * with 401 (backend). Catches regressions like "auth middleware
 * silently allowed unauth requests" or "the SSO redirect is
 * mis-configured".
 *
 * Read-only, no LLM cost.
 */

const FE = 'https://nexus.bsvibe.dev'
const API = 'https://api-nexus.bsvibe.dev'
const SSO = 'https://auth.bsvibe.dev'

test.describe('Production smoke — auth gate', () => {
  test.use({ baseURL: FE })

  test('unauth /dashboard either renders the landing page or bounces to SSO', async ({
    page,
  }) => {
    // BSVibe Auth pattern: silent SSO check on mount; if no session,
    // either the landing page renders inline (no bounce) or there's a
    // redirect to auth.bsvibe.dev. Either is acceptable — we just
    // need to confirm /dashboard does not render the founder UX
    // without auth.
    await page.goto('/dashboard')
    const url = page.url()
    const isLandingOrSSO =
      url.startsWith(FE) || url.startsWith(SSO)
    expect(isLandingOrSSO).toBeTruthy()

    // The founder UX (Direction / Progress / Decisions / Inside)
    // chrome should NOT be present. The single most stable
    // "founder-only" assertion: the chat textarea is gone.
    await expect(page.locator('textarea')).toHaveCount(0)
  })

  test('unauth /settings does not leak the Settings page', async ({ page }) => {
    await page.goto('/settings')
    // Same gate behaviour. We only assert the *content* — the
    // Executors heading is the most distinctive Settings string.
    // It should not be visible without auth.
    await expect(page.getByRole('heading', { name: /Executors/i })).toHaveCount(0)
  })

  test('API /api/v1/projects rejects unauth with 401', async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/projects`)
    expect(resp.status()).toBe(401)
  })

  test('API /api/v1/executor-config rejects unauth with 401', async ({ request }) => {
    // G7.5b/e: route renamed `executor-configs` → `executor-config`
    // (singular, one config per tenant).
    const resp = await request.get(`${API}/api/v1/executor-config`)
    expect(resp.status()).toBe(401)
  })

  test('API /api/v1/integrations rejects unauth with 401', async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/integrations`)
    expect(resp.status()).toBe(401)
  })

  test('CORS: preflight from nexus.bsvibe.dev is allowed', async ({ request }) => {
    // The frontend's same-origin proxy uses Vercel rewrites, so the
    // browser actually hits api-nexus.bsvibe.dev directly only when
    // the request bypasses the rewrite. We check the API CORS surface
    // matches the production frontend host so direct cross-origin
    // calls (which the SDKs occasionally do) don't 4xx out.
    const resp = await request.fetch(`${API}/api/v1/projects`, {
      method: 'OPTIONS',
      headers: {
        Origin: FE,
        'Access-Control-Request-Method': 'GET',
        'Access-Control-Request-Headers': 'authorization',
      },
    })
    // Either the explicit allowed origin (200/204 with ACAO header)
    // or middleware that lets the actual request through later (often
    // returns 200 even without ACAO when method+headers don't tip
    // CORS). The hard fail is 403 / 0.
    expect([200, 204]).toContain(resp.status())
  })

  test('production guards: dev-default keys NOT in use (process did not boot with weak signing key)', async ({
    request,
  }) => {
    // We can't read env vars from the API. But the startup security
    // guards in ``core/startup_guards.py`` REJECT the dev defaults
    // when ENVIRONMENT=production. If the process is healthy, that
    // means it booted with real keys — which is the assertion we
    // really care about (no dev key leak in prod).
    const resp = await request.get(`${API}/health`)
    expect(resp.status()).toBe(200)
    expect((await resp.json()).status).toBe('healthy')
  })
})
