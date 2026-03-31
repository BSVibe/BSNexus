/**
 * API route mocking helpers using page.route().
 * Intercepts all /api/v1/* calls so tests never hit a real backend.
 *
 * Playwright route priority: last registered wins when multiple patterns match.
 * Register catch-all FIRST, specific routes LAST.
 */
import type { Page } from '@playwright/test'
import {
  mockProjects,
  mockProjectsSummary,
  mockBoardResponse,
  mockSessions,
  mockUser,
} from './fixtures'

/** Inject auth tokens into localStorage so ProtectedRoute lets us through. */
export async function injectAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('bsnexus_access_token', 'mock-access-token-abc123')
    localStorage.setItem('bsnexus_refresh_token', 'mock-refresh-token-def456')
  })
}

/** Mock all standard API routes used across the app. */
export async function mockAllApis(page: Page) {
  // --- Catch-all first (lowest priority) ---
  await page.route('**/api/v1/**', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) })
  })

  // --- Specific routes (higher priority, registered after catch-all) ---

  // Auth user info
  await page.route('**/api/v1/auth/me', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockUser) })
  })

  // Projects list
  await page.route('**/api/v1/projects', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockProjects) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) })
  })

  // Projects summary (dashboard)
  await page.route('**/api/v1/dashboard/projects/summary', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockProjectsSummary) })
  })

  // Single project
  await page.route('**/api/v1/projects/proj-*', (route) => {
    const url = route.request().url()
    const id = url.split('/').pop()
    const project = mockProjects.find((p) => p.id === id)
    if (project) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(project) })
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) })
  })

  // Board SSE events endpoint
  await page.route('**/api/v1/board/proj-*/events', (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'data: {"event":"connected"}\n\n',
    })
  })

  // Board snapshot
  await page.route('**/api/v1/board/proj-*', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockBoardResponse) })
  })

  // Architect session by project (must be before generic session routes)
  await page.route('**/api/v1/architect/sessions/by-project/**', (route) => {
    const url = route.request().url()
    const projectId = url.split('/').pop()
    const session = mockSessions.find((s) => s.project_id === projectId)
    if (session) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(session) })
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) })
  })

  // Single architect session
  await page.route('**/api/v1/architect/sessions/session-*', (route) => {
    const url = route.request().url()
    const segments = url.split('/')
    const id = segments.find((s) => s.startsWith('session-'))
    const session = mockSessions.find((s) => s.id === id)
    if (session) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(session) })
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) })
  })

  // Architect sessions list
  await page.route('**/api/v1/architect/sessions', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockSessions) })
    }
    // POST — create session
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'session-new',
        project_id: null,
        name: null,
        status: 'active',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        messages: [],
      }),
    })
  })

  // PM control
  await page.route('**/api/v1/pm/**', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) })
  })
}

/** Setup page with auth + API mocks, then navigate. */
export async function setupPage(page: Page, path: string) {
  await injectAuth(page)
  await mockAllApis(page)
  await page.goto(path, { waitUntil: 'networkidle' })
}
