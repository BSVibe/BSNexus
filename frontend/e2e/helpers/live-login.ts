/**
 * Live E2E login helper — E2E bypass token injection.
 *
 * Instead of real SSO (which fails when bsserver:13100 isn't a registered
 * redirect URI on auth.bsvibe.dev), we inject the backend's E2E bypass
 * token into localStorage before navigation.
 *
 * The backend must have E2E_TEST_TOKEN set (e.g. "e2e-scenario-test-token").
 */
import type { Page } from '@playwright/test'

const LIVE_FRONTEND_URL = process.env.LIVE_FRONTEND_URL || 'http://bsserver:13100'
export const LIVE_API_URL = process.env.LIVE_API_URL || 'http://bsserver:18100'
export const skipUnlessLive = false

/** E2E bypass token — must match the backend's E2E_TEST_TOKEN env var. */
const E2E_TOKEN = process.env.E2E_TOKEN || 'e2e-scenario-test-token'

export async function loginAndNavigate(page: Page, path: string): Promise<void> {
  // Inject E2E token into localStorage BEFORE navigation so the frontend
  // picks it up from getAccessToken() without hitting auth.bsvibe.dev.
  await page.addInitScript((token: string) => {
    localStorage.setItem('bsnexus_access_token', token)
  }, E2E_TOKEN)

  await page.goto(`${LIVE_FRONTEND_URL}${path}`, {
    waitUntil: 'networkidle',
    timeout: 15_000,
  })

  // Give React time to mount and read the token from localStorage.
  await page.waitForTimeout(2_000)
}
