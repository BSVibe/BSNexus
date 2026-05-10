/**
 * API route mocking helpers for greenfield surfaces.
 *
 * Greenfield rebuild (decision-locks A3, 2026-05-08) collapsed nested
 * /projects/{id}/<sub> routes into flat /api/v1/<resource>?project_id=
 * shapes. The legacy mock pre-2026-05-09 also seeded agent / task /
 * worker / executor-config endpoints — those product surfaces retired
 * in the founder-metaphor migration and are not part of the greenfield
 * UX. This helper only mocks routes the current frontend actually
 * calls.
 *
 * Playwright route priority: last registered wins when multiple
 * patterns match. Register catch-all FIRST, specific routes LAST.
 */
import type { Page } from '@playwright/test'

const TENANT = 'tenant-001'
const USER_ID = 'user-001'

export const mockUser = {
  id: USER_ID,
  email: 'dev@bsvibe.dev',
  name: 'Test User',
  tenant_id: TENANT,
}

export const mockProject = {
  id: 'proj-001',
  tenant_id: TENANT,
  name: 'Test Project',
  description: null,
  status: 'active' as const,
  workspace_type: 'local_managed',
  github_repo_url: null,
  github_branch: null,
  repo_path: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
}

export const mockProjects = [mockProject]

/**
 * Block auth session check so unauthenticated pages stay on the
 * landing page. The useAuth hook calls auth.bsvibe.dev/api/session;
 * returning 401 keeps getAccessToken() resolving to null.
 */
export async function blockSSORedirect(page: Page) {
  await page.route('**/auth.bsvibe.dev/api/session', (route) => {
    return route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'no session' }),
    })
  })
}

function buildMockJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'ES256', typ: 'JWT' }))
  const body = btoa(JSON.stringify(payload))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
  return `${header}.${body}.mock-signature`
}

const MOCK_JWT_PAYLOAD = {
  sub: USER_ID,
  email: 'dev@bsvibe.dev',
  app_metadata: { tenant_id: TENANT, role: 'authenticated' },
  exp: Math.floor(Date.now() / 1000) + 3600,
}

/**
 * Inject a mock access token into localStorage + mock the SSO session
 * endpoint, before the page navigates. ``useAuth.getAccessToken()``
 * checks localStorage first, so this avoids the cross-origin session
 * fetch race entirely.
 */
export async function injectAuth(page: Page) {
  const accessToken = buildMockJwt(MOCK_JWT_PAYLOAD)
  await page.addInitScript(
    ({ token }) => {
      localStorage.setItem('bsnexus_access_token', token)
      localStorage.setItem('bsnexus_refresh_token', 'mock-refresh-token')
      localStorage.setItem('bsnexus_expires_at', String(Date.now() + 3600 * 1000))
    },
    { token: accessToken },
  )
  await page.route('**/auth.bsvibe.dev/api/session', (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user: mockUser,
        tenants: [
          {
            id: TENANT,
            name: 'Test Tenant',
            slug: 'test',
            plan: 'team',
            type: 'company',
            role: 'admin',
          },
        ],
        active_tenant_id: TENANT,
        access_token: accessToken,
        refresh_token: 'mock-refresh-token',
        expires_in: 3600,
      }),
    })
  })
}

const emptyBrief = (projectId: string | null) => ({
  scope: projectId ? 'project' : 'company',
  project_id: projectId,
  sections: {
    shipped: [],
    needs_decision: [],
    blocked: [],
    running: [],
    next: [],
  },
  generated_at: new Date().toISOString(),
})

/**
 * Mock greenfield surface routes. Returns empty payloads for the
 * resources the current UI calls on a clean tenant — projects,
 * directions, requests, decisions, deliverables, brief, runs, events,
 * workspace files, integrations. Tests that need richer state should
 * layer ``installFounderMocks`` on top.
 */
export async function mockAllApis(page: Page) {
  // Catch-all first so specific routes take priority.
  await page.route('**/api/v1/**', (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    })
  })

  await page.route('**/api/v1/auth/me', (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(mockUser),
    })
  })

  await page.route('**/api/v1/projects', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockProjects),
      })
    }
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(mockProject),
      })
    }
    return route.fulfill({ status: 405, body: '' })
  })

  await page.route('**/api/v1/projects/proj-*', (route) => {
    const url = route.request().url()
    const id = url.split('/').pop()?.split('?')[0]
    const project = mockProjects.find((p) => p.id === id)
    if (project) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(project),
      })
    }
    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Not found' }),
    })
  })

  // Greenfield Direction primitive (G1, flat A3).
  await page.route(/\/api\/v1\/directions(\?|$)/, (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          direction: {
            id: 'dir-mock',
            tenant_id: TENANT,
            project_id: mockProject.id,
            source: 'mobile_web',
            actor_id: USER_ID,
            body: '',
            target_hint: null,
            created_at: new Date().toISOString(),
          },
          request: null,
          routing: null,
          acknowledgement: 'Direction accepted.',
        }),
      })
    }
    return route.fulfill({ status: 405, body: '' })
  })

  // Brief, Decisions, Deliverables, Requests, Runs — flat empty defaults.
  await page.route(/\/api\/v1\/brief(\?|$)/, (route) => {
    const url = route.request().url()
    const m = url.match(/[?&]project_id=([^&]+)/)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(emptyBrief(m ? decodeURIComponent(m[1]) : null)),
    })
  })

  await page.route(/\/api\/v1\/decisions(\?|$)/, (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  await page.route(/\/api\/v1\/deliverables(\?|$)/, (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  await page.route(/\/api\/v1\/requests(\?|$)/, (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  await page.route(/\/api\/v1\/runs(\?|$)/, (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  // Per-project SSE event stream — empty connected.
  await page.route(/\/api\/v1\/events(\?|$)/, (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'event: connected\ndata: {}\n\n',
    })
  })

  // Workspace files — empty tree default.
  await page.route(/\/api\/v1\/workspace-files\b.*/, (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  // Integrations admin (REVIEW_LATER per disposition; redacted defaults).
  await page.route('**/api/v1/integrations', (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        bsage: { enabled: false, has_api_key: false, base_url: null },
        bsgateway: { enabled: false, has_api_key: false, base_url: null },
        bsupervisor: { enabled: false, has_api_key: false, base_url: null },
      }),
    })
  })
}

/** Setup page with auth + greenfield API mocks, then navigate. */
export async function setupPage(page: Page, path: string) {
  await injectAuth(page)
  await mockAllApis(page)
  await page.goto(path, { waitUntil: 'networkidle' })
}
