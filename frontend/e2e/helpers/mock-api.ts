import { Page } from '@playwright/test'

// ─── Auth ───────────────────────────────────────────────────────────
export const MOCK_USER = {
  id: 'user-001',
  email: 'test@example.com',
  role: 'admin',
  app_metadata: {},
}

export async function setupAuth(page: Page) {
  // Set localStorage tokens before navigating
  await page.addInitScript(() => {
    localStorage.setItem('bsnexus_access_token', 'mock-access-token')
    localStorage.setItem('bsnexus_refresh_token', 'mock-refresh-token')
  })
}

// ─── Mock Data ──────────────────────────────────────────────────────
export const MOCK_PROJECTS = [
  {
    id: 'proj-1',
    name: 'Test Project Alpha',
    description: 'A test project for E2E testing',
    design_doc_path: null,
    repo_path: '/workspace/alpha',
    status: 'active' as const,
    llm_config: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-15T00:00:00Z',
    phases: [
      {
        id: 'phase-1',
        project_id: 'proj-1',
        name: 'Phase 1',
        description: 'Initial phase',
        branch_name: 'phase-1',
        order: 1,
        status: 'active' as const,
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-01T00:00:00Z',
      },
    ],
  },
  {
    id: 'proj-2',
    name: 'Test Project Beta',
    description: 'Another project for testing',
    design_doc_path: null,
    repo_path: '/workspace/beta',
    status: 'design' as const,
    llm_config: null,
    created_at: '2025-01-05T00:00:00Z',
    updated_at: '2025-01-20T00:00:00Z',
    phases: [],
  },
]

export const MOCK_PROJECTS_SUMMARY = [
  {
    id: 'proj-1',
    name: 'Test Project Alpha',
    status: 'active',
    task_counts: { ready: 2, in_progress: 1, review: 1, done: 3, waiting: 0 },
    bug_count: 1,
    current_phase: 'Phase 1',
    has_architect_session: true,
    last_activity: '2025-01-15T00:00:00Z',
  },
  {
    id: 'proj-2',
    name: 'Test Project Beta',
    status: 'design',
    task_counts: {},
    bug_count: 0,
    current_phase: null,
    has_architect_session: false,
    last_activity: null,
  },
]

export const MOCK_DASHBOARD_STATS = {
  total_projects: 2,
  active_projects: 1,
  completed_projects: 0,
  total_tasks: 7,
  active_tasks: 3,
  in_progress_tasks: 1,
  done_tasks: 3,
  completion_rate: 43,
}

function makeMockTask(overrides: Record<string, unknown> = {}) {
  return {
    id: 'task-1',
    project_id: 'proj-1',
    phase_id: 'phase-1',
    title: 'Implement authentication',
    description: 'Add auth flow',
    status: 'ready',
    priority: 'high',
    task_type: 'feature',
    source: 'architect',
    parent_task_id: null,
    worker_prompt: null,
    qa_prompt: null,
    branch_name: null,
    commit_hash: null,
    qa_result: null,
    output_path: null,
    error_message: null,
    retry_count: 0,
    max_retries: 3,
    qa_feedback_history: null,
    version: 1,
    created_at: '2025-01-02T00:00:00Z',
    updated_at: '2025-01-02T00:00:00Z',
    started_at: null,
    completed_at: null,
    depends_on: [],
    ...overrides,
  }
}

export const MOCK_TASKS = {
  ready: [
    makeMockTask({ id: 'task-1', title: 'Implement authentication', priority: 'high', task_type: 'feature' }),
    makeMockTask({ id: 'task-2', title: 'Add input validation', priority: 'medium', task_type: 'improvement' }),
  ],
  in_progress: [
    makeMockTask({ id: 'task-3', title: 'Build API endpoints', status: 'in_progress', priority: 'high', task_type: 'feature' }),
  ],
  review: [
    makeMockTask({ id: 'task-4', title: 'Fix login bug', status: 'review', priority: 'critical', task_type: 'bug' }),
  ],
  done: [
    makeMockTask({ id: 'task-5', title: 'Setup CI/CD', status: 'done', priority: 'medium', task_type: 'chore' }),
    makeMockTask({ id: 'task-6', title: 'Create database schema', status: 'done', priority: 'high', task_type: 'feature' }),
    makeMockTask({ id: 'task-7', title: 'Write unit tests', status: 'done', priority: 'medium', task_type: 'test' }),
  ],
  waiting: [],
}

export const MOCK_BOARD_RESPONSE = {
  project_id: 'proj-1',
  columns: {
    waiting: { tasks: MOCK_TASKS.waiting },
    ready: { tasks: MOCK_TASKS.ready },
    in_progress: { tasks: MOCK_TASKS.in_progress },
    review: { tasks: MOCK_TASKS.review },
    done: { tasks: MOCK_TASKS.done },
  },
  stats: { waiting: 0, ready: 2, in_progress: 1, review: 1, done: 3 },
  phases: {
    'phase-1': { name: 'Phase 1', order: 1, status: 'active' },
  },
  redesign_tasks: [],
}

export const MOCK_SESSIONS = [
  {
    id: 'session-1',
    project_id: 'proj-1',
    name: 'Design Session Alpha',
    status: 'project_bound',
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    messages: [
      {
        id: 'msg-1',
        session_id: 'session-1',
        role: 'user',
        content: 'Design a task management system',
        created_at: '2025-01-01T00:00:00Z',
      },
      {
        id: 'msg-2',
        session_id: 'session-1',
        role: 'assistant',
        content: 'I will design a task management system with the following components...',
        created_at: '2025-01-01T00:01:00Z',
      },
    ],
  },
  {
    id: 'session-2',
    project_id: null,
    name: 'New Design Session',
    status: 'active',
    created_at: '2025-01-10T00:00:00Z',
    updated_at: '2025-01-10T00:00:00Z',
    messages: [],
  },
]

// ─── Route Setup ────────────────────────────────────────────────────

export async function mockAuthRoutes(page: Page) {
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) })
  )
  await page.route('**/api/v1/auth/refresh', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: 'new-access', refresh_token: 'new-refresh' }),
    })
  )
  await page.route('**/api/v1/auth/logout', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) })
  )
}

export async function mockDashboardRoutes(page: Page) {
  await page.route('**/api/v1/projects', (route, request) => {
    if (request.method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_PROJECTS) })
    }
    if (request.method() === 'POST') {
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(MOCK_PROJECTS[0]) })
    }
    if (request.method() === 'DELETE') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) })
    }
    return route.continue()
  })

  await page.route('**/api/v1/projects/batch-delete', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ deleted: 2 }) })
  )

  await page.route('**/api/v1/dashboard/stats', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DASHBOARD_STATS) })
  )

  await page.route('**/api/v1/dashboard/projects-summary', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_PROJECTS_SUMMARY) })
  )
}

export async function mockProjectRoutes(page: Page) {
  await page.route('**/api/v1/projects/proj-*', (route, request) => {
    if (request.method() === 'GET') {
      const url = request.url()
      const id = url.match(/projects\/(proj-\d+)/)?.[1]
      const project = MOCK_PROJECTS.find((p) => p.id === id)
      return route.fulfill({
        status: project ? 200 : 404,
        contentType: 'application/json',
        body: JSON.stringify(project || { detail: 'Not found' }),
      })
    }
    if (request.method() === 'DELETE') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) })
    }
    return route.continue()
  })
}

export async function mockBoardRoutes(page: Page) {
  await page.route('**/api/v1/board/proj-*/events', (route) => {
    // SSE endpoint - return empty stream that closes
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'data: {"event":"connected"}\n\n',
    })
  })

  await page.route('**/api/v1/board/proj-*', (route, request) => {
    if (request.method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_BOARD_RESPONSE),
      })
    }
    return route.continue()
  })
}

export async function mockArchitectRoutes(page: Page) {
  await page.route('**/api/v1/architect/sessions/batch-delete', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ deleted: 1 }) })
  )

  await page.route('**/api/v1/architect/sessions/by-project/*', (route) => {
    const session = MOCK_SESSIONS[0]
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(session) })
  })

  await page.route('**/api/v1/architect/sessions/*/message/stream', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'event: chunk\ndata: Here is my response\n\nevent: done\ndata: Here is my response\n\n',
    })
  })

  await page.route('**/api/v1/architect/sessions/*', (route, request) => {
    if (request.method() === 'GET') {
      const url = request.url()
      const id = url.match(/sessions\/([^/]+)/)?.[1]
      const session = MOCK_SESSIONS.find((s) => s.id === id) || MOCK_SESSIONS[0]
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(session) })
    }
    if (request.method() === 'DELETE') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) })
    }
    return route.continue()
  })

  await page.route('**/api/v1/architect/sessions', (route, request) => {
    if (request.method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSIONS) })
    }
    if (request.method() === 'POST') {
      const newSession = {
        id: 'session-new',
        project_id: null,
        name: 'New Session',
        status: 'active',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        messages: [],
      }
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(newSession) })
    }
    return route.continue()
  })
}

/** Set up all API mocks for authenticated pages */
export async function setupMocks(page: Page) {
  await setupAuth(page)
  await mockAuthRoutes(page)
  await mockDashboardRoutes(page)
  await mockProjectRoutes(page)
  await mockBoardRoutes(page)
  await mockArchitectRoutes(page)
}
