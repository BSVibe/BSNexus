/**
 * Real (no-mock) live e2e helper.
 *
 * Unlike ``live-setup.ts`` which mocks auth + a handful of routes and only
 * lets specific endpoints reach the backend, this helper mocks **nothing**.
 * It assumes a real backend (with ``E2E_TEST_TOKEN`` env var set to the
 * value exported from ``live-token.ts``) is running on the URL configured
 * via ``LIVE_API_URL``. The browser session is bootstrapped by writing the
 * shared bypass token into ``localStorage`` before navigation, so the app
 * picks it up the same way it would pick up a real session.
 *
 * Skipped automatically when ``LIVE_API_URL`` is not configured — these
 * specs only run in CI / when an operator explicitly opts in.
 */
import type { Page } from '@playwright/test'

import { E2E_TEST_TOKEN, STORED_TOKEN_KEY } from './live-token'

export const LIVE_API_URL = process.env.LIVE_API_URL || ''
export const LIVE_FRONTEND_URL = process.env.LIVE_FRONTEND_URL || ''

export const skipUnlessLive = LIVE_API_URL && LIVE_FRONTEND_URL ? false : true

export async function setupRealLivePage(page: Page, path: string): Promise<void> {
  // Inject the bypass token *before* navigation so getAccessToken() picks
  // it up on first mount.
  await page.addInitScript(
    ({ key, token }) => {
      try {
        localStorage.setItem(key, token)
      } catch {
        /* SecurityError on file:// — irrelevant for our base URLs */
      }
    },
    { key: STORED_TOKEN_KEY, token: E2E_TEST_TOKEN },
  )
  await page.goto(`${LIVE_FRONTEND_URL}${path}`, { waitUntil: 'domcontentloaded' })
}
