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
  mockExecutorConfigs,
  mockInstallToken,
} from './fixtures'

/**
 * Block auth session check so unauthenticated pages stay on the landing page.
 * The useAuth hook calls auth.bsvibe.dev/api/session to check for an existing
 * session cookie. We return 401 so getAccessToken() returns null.
 */
export async function blockSSORedirect(page: Page) {
  await page.route('**/auth.bsvibe.dev/api/session', (route) => {
    return route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'no session' }) })
  })
}

/**
 * Build a fake JWT with the given payload (header.payload.signature).
 * Only the payload section is read by decodeJwt(); header and signature are stubs.
 */
function buildMockJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'ES256', typ: 'JWT' }))
  const body = btoa(JSON.stringify(payload)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `${header}.${body}.mock-signature`
}

const MOCK_JWT_PAYLOAD = {
  sub: 'user-001',
  email: 'dev@bsvibe.dev',
  app_metadata: { tenant_id: 'tenant-001', role: 'authenticated' },
  exp: Math.floor(Date.now() / 1000) + 3600,
}

/**
 * Mock the auth.bsvibe.dev/api/session endpoint to return a valid session,
 * so getAccessToken() succeeds and useAuth() resolves a user.
 */
export async function injectAuth(page: Page) {
  const mockAccessToken = buildMockJwt(MOCK_JWT_PAYLOAD)
  await page.route('**/auth.bsvibe.dev/api/session', (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: mockAccessToken,
        refresh_token: 'mock-refresh-token-def456',
        expires_in: 3600,
      }),
    })
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

  // Project chat SSE events endpoint
  await page.route('**/api/v1/projects/proj-*/chat/events', (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'event: connected\ndata: {}\n\n',
    })
  })

  // Project chat history / send / clear
  await page.route('**/api/v1/projects/proj-*/chat', (route) => {
    const method = route.request().method()
    if (method === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) })
    }
    if (method === 'DELETE') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ detail: 'cleared' }) })
    }
    // POST — fire-and-forget, returns dispatched agent names
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ dispatched_agents: ['CEO'] }),
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

  // Executor configs (CRUD)
  await page.route('**/api/v1/executor-configs', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockExecutorConfigs) })
    }
    // POST — create
    return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(mockExecutorConfigs[0]) })
  })
  await page.route('**/api/v1/executor-configs/*', (route) => {
    const url = route.request().url()
    const id = url.split('/').pop()
    const config = mockExecutorConfigs.find((c) => c.id === id)
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ status: 204, body: '' })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(config || mockExecutorConfigs[0]) })
  })

  // Install token
  await page.route('**/api/v1/settings/install-token', (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockInstallToken) })
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
