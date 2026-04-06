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
  mockAgents,
  mockOrgChart,
  mockWorkers,
  mockGoals,
  mockBudgetOverview,
  mockCostRecords,
  mockGlobalSettings,
} from './fixtures'

/**
 * Block SSO silent-check redirect so unauthenticated pages don't navigate away.
 * The BSVibeAuth SDK redirects to auth.bsvibe.dev/api/silent-check when no
 * local session exists. We intercept this and redirect back with ?sso_error=1
 * so checkSession() returns null instead of 'redirect'.
 */
export async function blockSSORedirect(page: Page) {
  await page.route('**/api/silent-check*', (route) => {
    const url = new URL(route.request().url())
    const redirectUri = url.searchParams.get('redirect_uri') || 'http://localhost:3000/'
    const separator = redirectUri.includes('?') ? '&' : '?'
    return route.fulfill({
      status: 302,
      headers: { location: `${redirectUri}${separator}sso_error=1` },
    })
  })
}

/** Inject auth tokens into localStorage so ProtectedRoute lets us through.
 *
 * BSVibeAuth SDK stores user as JSON under 'bsvibe_user' key.
 * The user object must match BSVibeUser interface:
 *   { id, email, tenantId, role, accessToken, refreshToken, expiresAt }
 */
export async function injectAuth(page: Page) {
  await page.addInitScript(() => {
    const bsvibeUser = {
      id: 'user-001',
      email: 'dev@bsvibe.dev',
      tenantId: 'tenant-001',
      role: 'authenticated',
      accessToken: 'mock-access-token-abc123',
      refreshToken: 'mock-refresh-token-def456',
      expiresAt: Math.floor(Date.now() / 1000) + 3600, // 1 hour from now
    }
    localStorage.setItem('bsvibe_user', JSON.stringify(bsvibeUser))
  })
  // Mock auth/me so enrichUser() resolves
  await page.route('**/api/v1/auth/me', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockUser) })
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
  await page.route('**/api/v1/dashboard/projects-summary', (route) => {
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

  // Agents org chart
  await page.route('**/api/v1/agents/org-chart', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockOrgChart) })
  })

  // Agents list (glob must match query params like ?active_only=true)
  await page.route('**/api/v1/agents?*', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockAgents) })
  })
  await page.route('**/api/v1/agents', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockAgents) })
    }
    // POST — create agent
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ ...mockAgents[0], id: 'agent-new', name: 'New Agent' }),
    })
  })

  // Single agent
  await page.route('**/api/v1/agents/agent-*', (route) => {
    const url = route.request().url()
    const id = url.split('/').pop()
    const agent = mockAgents.find((a) => a.id === id)
    if (agent) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(agent) })
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) })
  })

  // Workers
  await page.route('**/api/v1/workers', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockWorkers) })
  })

  // Goals
  await page.route('**/api/v1/goals?*', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockGoals) })
  })
  await page.route('**/api/v1/goals', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockGoals) })
  })

  // Budget summary
  await page.route('**/api/v1/budget/summary', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockBudgetOverview) })
  })

  // Budget records
  await page.route('**/api/v1/budget/records*', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockCostRecords) })
  })

  // Budget reset
  await page.route('**/api/v1/budget/reset', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ reset_count: 3 }) })
  })

  // Settings
  await page.route('**/api/v1/settings', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockGlobalSettings) })
    }
    // PUT — update settings
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockGlobalSettings) })
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
