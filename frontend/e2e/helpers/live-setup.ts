/**
 * Live E2E setup — auth mocked, target API routes hit real backend.
 * Requires: backend at localhost:8000, frontend at localhost:3000
 *
 * IMPORTANT: The mock auth token is NOT valid on the real backend.
 * Any authenticated endpoint that the app calls globally (on mount) must be
 * mocked to prevent 401 → signOut → redirect to Landing.
 *
 * Routes that are safe to hit real backend:
 * - /api/v1/agents (no global auth guard, returns list)
 * - /api/v1/budget/* (no global auth guard)
 * - /api/v1/agents/org-chart
 *
 * Routes that MUST be mocked (authenticated, called on app init):
 * - /api/v1/auth/me
 * - /api/v1/settings (SettingsPage fetches on mount, 401 kills session)
 * - /api/v1/dashboard/* (DashboardPage fetches on mount)
 * - /api/v1/projects (DashboardPage project list)
 */
import type { Page } from '@playwright/test'
import { injectAuth } from './mock-api'

const API = 'http://localhost:8000'

export { API }

/**
 * Setup auth + minimal mocks for live E2E testing.
 * Call this in beforeEach or directly before page.goto().
 */
export async function setupLiveAuth(page: Page) {
  await injectAuth(page)

  // Auth/me
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'user-001', email: 'dev@bsvibe.dev' }),
    }),
  )

  // Settings (called globally on some pages — 401 would kill session)
  await page.route('**/api/v1/settings', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        llm_api_key: null,
        llm_model: null,
        llm_base_url: null,
        default_executor_type: 'claude_api',
      }),
    }),
  )

  // Dashboard summary (called on dashboard mount)
  await page.route('**/api/v1/dashboard/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  )

  // Projects list (called on dashboard mount)
  await page.route('**/api/v1/projects', (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    return route.continue()
  })
}

/**
 * Full setup + navigate for live E2E.
 */
export async function setupLivePage(page: Page, path: string) {
  await setupLiveAuth(page)
  await page.goto(path, { waitUntil: 'networkidle' })
}
