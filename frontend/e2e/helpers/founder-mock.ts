/**
 * Founder-surface mock helper (greenfield, flat A3 routes).
 *
 * Tests build up a small state object describing what the tenant looks
 * like on the wire — projects, requests, decisions, deliverables, runs,
 * SSE events — then install Playwright route handlers that serve that
 * state. Route shapes match the flat /api/v1/<resource>?project_id=
 * endpoints in CLAUDE.md (decision-locks A3, 2026-05-08).
 *
 * The legacy nested /api/v1/projects/{id}/<sub> routes and the agent /
 * task / executor / worker / Inside-panel surfaces retired with the
 * file-disposition.md greenfield purge.
 *
 * SSE: the per-project event stream is stubbed by emitting every entry
 * in ``state.sseEvents`` as a single response body. The browser's
 * EventSource parses each ``event:``+``data:`` block in order.
 */
import type { Page, Route } from '@playwright/test'

const TENANT = 'tenant-001'
const ACTOR_ID = 'user-001'

// ─── Domain shapes ───────────────────────────────────────────────────

export type MockRequestStatus =
  | 'open'
  | 'running'
  | 'blocked'
  | 'review_ready'
  | 'shipped'
  | 'abandoned'

export interface MockRequest {
  id: string
  tenant_id: string
  project_id: string
  origin_message_id: string | null
  intent_summary: string
  status: MockRequestStatus
  user_confirmed: boolean
  superseded_by_id: string | null
  composition_root_id: string | null
  created_at: string
  updated_at: string
}

export type MockRunStatus = 'pending' | 'running' | 'blocked' | 'done'

export interface MockExecutionRun {
  id: string
  tenant_id: string
  project_id: string
  request_id: string | null
  status: MockRunStatus
  priority: 'low' | 'medium' | 'high'
  estimated_cost_cents: number
  actual_cost_cents: number
  error_message: string | null
  retry_count: number
  max_retries: number
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

export interface MockDecision {
  id: string
  tenant_id: string
  project_id: string
  request_id: string | null
  origin_run_id: string | null
  question: string
  options: string[]
  blocking: boolean
  resolution: string | null
  resolved_by: string | null
  resolved_at: string | null
  created_at: string
}

export type MockProofState =
  | 'verification_missing'
  | 'verifying'
  | 'verified'
  | 'verification_failed'
  | 'human_review_required'
  | 'not_applicable'

export interface MockDeliverable {
  id: string
  tenant_id: string
  project_id: string
  request_id: string | null
  type: 'code' | 'doc' | 'design' | 'data' | 'url'
  title: string
  status: 'draft' | 'ready' | 'delivered'
  current_version_id: string | null
  created_at: string
  updated_at: string
  proof_state: MockProofState
  verifier_type: string | null
  verifier_inputs: Record<string, unknown> | null
  verification_exit_code: number | null
  proof_summary: string | null
  proof_refs: Array<{ label: string; type: string; href: string }> | null
  risk_summary: string | null
  verified_at: string | null
  artifact_refs: Array<{ path: string; kind?: string }>
}

export interface FounderMockState {
  projectId: string
  projectName: string
  requests: MockRequest[]
  runs: MockExecutionRun[]
  decisions: MockDecision[]
  deliverables: MockDeliverable[]
  /** Pre-recorded SSE blocks delivered when the client opens
   *  ``/api/v1/events?project_id=…``. Each entry is one
   *  ``event:``+``data:`` pair. */
  sseEvents: string[]
  /** Captured: appended to whenever a mutating handler fires
   *  (decisions/resolve, deliverables/verify, directions). */
  posts: Array<{ url: string; body: unknown }>
}

// ─── Builders ────────────────────────────────────────────────────────

const now = () => new Date().toISOString()

export function makeRequest(p: Partial<MockRequest> & { id: string; project_id: string }): MockRequest {
  return {
    id: p.id,
    tenant_id: p.tenant_id ?? TENANT,
    project_id: p.project_id,
    origin_message_id: p.origin_message_id ?? null,
    intent_summary: p.intent_summary ?? 'Mock request',
    status: p.status ?? 'open',
    user_confirmed: p.user_confirmed ?? false,
    superseded_by_id: p.superseded_by_id ?? null,
    composition_root_id: p.composition_root_id ?? null,
    created_at: p.created_at ?? now(),
    updated_at: p.updated_at ?? now(),
  }
}

export function makeRun(p: Partial<MockExecutionRun> & { id: string; project_id: string }): MockExecutionRun {
  return {
    id: p.id,
    tenant_id: p.tenant_id ?? TENANT,
    project_id: p.project_id,
    request_id: p.request_id ?? null,
    status: p.status ?? 'running',
    priority: p.priority ?? 'medium',
    estimated_cost_cents: p.estimated_cost_cents ?? 0,
    actual_cost_cents: p.actual_cost_cents ?? 0,
    error_message: p.error_message ?? null,
    retry_count: p.retry_count ?? 0,
    max_retries: p.max_retries ?? 3,
    created_at: p.created_at ?? now(),
    updated_at: p.updated_at ?? now(),
    started_at: p.started_at ?? null,
    completed_at: p.completed_at ?? null,
  }
}

export function makeDecision(
  p: Partial<MockDecision> & { id: string; project_id: string; question: string },
): MockDecision {
  return {
    id: p.id,
    tenant_id: p.tenant_id ?? TENANT,
    project_id: p.project_id,
    request_id: p.request_id ?? null,
    origin_run_id: p.origin_run_id ?? null,
    question: p.question,
    options: p.options ?? [],
    blocking: p.blocking ?? false,
    resolution: p.resolution ?? null,
    resolved_by: p.resolved_by ?? null,
    resolved_at: p.resolved_at ?? null,
    created_at: p.created_at ?? now(),
  }
}

export function makeDeliverable(
  p: Partial<MockDeliverable> & { id: string; project_id: string; title: string },
): MockDeliverable {
  return {
    id: p.id,
    tenant_id: p.tenant_id ?? TENANT,
    project_id: p.project_id,
    request_id: p.request_id ?? null,
    type: p.type ?? 'code',
    title: p.title,
    status: p.status ?? 'delivered',
    current_version_id: p.current_version_id ?? null,
    created_at: p.created_at ?? now(),
    updated_at: p.updated_at ?? now(),
    proof_state: p.proof_state ?? 'verified',
    verifier_type: p.verifier_type ?? 'python_test',
    verifier_inputs: p.verifier_inputs ?? null,
    verification_exit_code: p.verification_exit_code ?? 0,
    proof_summary: p.proof_summary ?? null,
    proof_refs: p.proof_refs ?? null,
    risk_summary: p.risk_summary ?? null,
    verified_at: p.verified_at ?? null,
    artifact_refs: p.artifact_refs ?? [],
  }
}

export function makeFounderState(projectId: string, projectName = 'Test Project'): FounderMockState {
  return {
    projectId,
    projectName,
    requests: [],
    runs: [],
    decisions: [],
    deliverables: [],
    sseEvents: [],
    posts: [],
  }
}

/** Build a single SSE block (`event:` + `data:` pair). */
export function sseEvent(eventType: string, payload: Record<string, unknown>): string {
  return `event: ${eventType}\ndata: ${JSON.stringify(payload)}\n\n`
}

// ─── Route installer ─────────────────────────────────────────────────

interface ListQuery {
  projectId?: string
  requestId?: string
  blockingOnly?: boolean
  resolved?: boolean
}

function parseQuery(url: string): ListQuery {
  const u = new URL(url, 'http://localhost')
  const out: ListQuery = {}
  const projectId = u.searchParams.get('project_id')
  if (projectId) out.projectId = projectId
  const requestId = u.searchParams.get('request_id')
  if (requestId) out.requestId = requestId
  const blockingOnly = u.searchParams.get('blocking_only')
  if (blockingOnly === 'true') out.blockingOnly = true
  const resolved = u.searchParams.get('resolved')
  if (resolved === 'true') out.resolved = true
  if (resolved === 'false') out.resolved = false
  return out
}

export async function installFounderMocks(page: Page, state: FounderMockState): Promise<void> {
  const pid = state.projectId

  const projectShape = () => ({
    id: pid,
    tenant_id: TENANT,
    name: state.projectName,
    description: null,
    status: 'active',
    workspace_type: 'local_managed',
    github_repo_url: null,
    github_branch: null,
    repo_path: null,
    created_at: now(),
    updated_at: now(),
  })

  await page.route('**/api/v1/projects', (route: Route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(projectShape()),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([projectShape()]),
    })
  })

  await page.route(`**/api/v1/projects/${pid}*`, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(projectShape()),
    })
  })

  // Direction primitive (G1).
  await page.route(/\/api\/v1\/directions(\?|$)/, async (route: Route) => {
    const req = route.request()
    if (req.method() === 'POST') {
      const body = (req.postDataJSON?.() ?? {}) as Record<string, unknown>
      state.posts.push({ url: req.url(), body })
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          direction: {
            id: `dir-${state.posts.length}`,
            tenant_id: TENANT,
            project_id: (body.project_id as string | null) ?? pid,
            source: (body.source as string) ?? 'web',
            actor_id: ACTOR_ID,
            body: (body.body as string) ?? '',
            target_hint: (body.target_hint as string | null) ?? null,
            created_at: now(),
          },
          request: null,
          routing: null,
          acknowledgement: 'Direction accepted.',
        }),
      })
    }
    return route.fulfill({ status: 405, body: '' })
  })

  // Requests — flat: GET /api/v1/requests?project_id=… or ?request_id=…
  await page.route(/\/api\/v1\/requests(\?|$)/, (route: Route) => {
    const q = parseQuery(route.request().url())
    let rows = state.requests
    if (q.projectId) rows = rows.filter((r) => r.project_id === q.projectId)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(rows),
    })
  })

  // Runs — flat: GET /api/v1/runs?request_id=…
  await page.route(/\/api\/v1\/runs(\?|$)/, (route: Route) => {
    const q = parseQuery(route.request().url())
    let rows = state.runs
    if (q.requestId) rows = rows.filter((r) => r.request_id === q.requestId)
    if (q.projectId) rows = rows.filter((r) => r.project_id === q.projectId)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(rows),
    })
  })

  // Decisions — flat: GET /api/v1/decisions?project_id=…&blocking_only=…
  await page.route(/\/api\/v1\/decisions(\?|$)/, (route: Route) => {
    const q = parseQuery(route.request().url())
    let rows = state.decisions
    if (q.projectId) rows = rows.filter((d) => d.project_id === q.projectId)
    if (q.blockingOnly) rows = rows.filter((d) => d.blocking && !d.resolved_at)
    if (q.resolved === true) rows = rows.filter((d) => d.resolved_at !== null)
    if (q.resolved === false) rows = rows.filter((d) => d.resolved_at === null)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(rows),
    })
  })

  await page.route(/\/api\/v1\/decisions\/[^/]+\/resolve(\?|$)/, async (route: Route) => {
    const req = route.request()
    const body = (req.postDataJSON?.() ?? {}) as { resolution?: string; resolved_by?: string }
    state.posts.push({ url: req.url(), body })
    const decisionId = req.url().match(/decisions\/([^/]+)\/resolve/)?.[1]
    const target = state.decisions.find((d) => d.id === decisionId)
    if (target) {
      target.resolution = body.resolution ?? ''
      target.resolved_by = body.resolved_by ?? ACTOR_ID
      target.resolved_at = now()
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(target ?? { detail: 'not found' }),
    })
  })

  // Deliverables — flat: GET /api/v1/deliverables?project_id=…
  await page.route(/\/api\/v1\/deliverables(\?|$)/, (route: Route) => {
    const q = parseQuery(route.request().url())
    let rows = state.deliverables
    if (q.projectId) rows = rows.filter((d) => d.project_id === q.projectId)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(rows),
    })
  })

  await page.route(/\/api\/v1\/deliverables\/[^/]+\/verify(\?|$)/, async (route: Route) => {
    const req = route.request()
    state.posts.push({ url: req.url(), body: {} })
    const id = req.url().match(/deliverables\/([^/]+)\/verify/)?.[1]
    const target = state.deliverables.find((d) => d.id === id)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(target ?? { detail: 'not found' }),
    })
  })

  // Brief — flat: GET /api/v1/brief?project_id=…
  // G7.1 wire shape: ``{scope, project_id, sections, generated_at}``.
  // Sections are typed per-card; ``blocked`` is a discriminated union.
  await page.route(/\/api\/v1\/brief(\?|$)/, (route: Route) => {
    const q = parseQuery(route.request().url())
    const scopeProjectId = q.projectId ?? null
    const inScope = <T extends { project_id: string }>(rows: T[]): T[] =>
      scopeProjectId ? rows.filter((r) => r.project_id === scopeProjectId) : rows

    const deliverableCard = (d: MockDeliverable) => ({
      id: d.id,
      project_id: d.project_id,
      request_id: d.request_id,
      title: d.title,
      type: d.type,
      proof_state: d.proof_state,
      proof_summary: d.proof_summary,
      verifier_type: d.verifier_type,
      verified_at: d.verified_at,
      created_at: d.created_at,
      artifact_refs: d.artifact_refs ?? [],
    })
    const requestCard = (r: MockRequest) => ({
      id: r.id,
      project_id: r.project_id,
      intent: r.intent_summary,
      status: r.status,
      created_at: r.created_at,
      updated_at: r.updated_at,
    })
    const decisionCard = (d: MockDecision) => ({
      id: d.id,
      project_id: d.project_id,
      question: d.question,
      blocking: d.blocking,
      created_at: d.created_at,
    })

    const blocked: Array<Record<string, unknown>> = []
    for (const r of inScope(state.requests).filter((r) => r.status === 'blocked')) {
      blocked.push({ ...requestCard(r), kind: 'request' })
    }
    for (const d of inScope(state.deliverables).filter(
      (d) =>
        d.proof_state === 'verification_failed' ||
        d.proof_state === 'verification_missing' ||
        d.proof_state === 'human_review_required',
    )) {
      blocked.push({ ...deliverableCard(d), kind: 'deliverable' })
    }

    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        scope: scopeProjectId ? 'project' : 'company',
        project_id: scopeProjectId,
        sections: {
          shipped: inScope(state.deliverables)
            .filter((d) => d.proof_state === 'verified' && d.status === 'delivered')
            .map(deliverableCard),
          needs_decision: inScope(state.decisions)
            .filter((d) => d.blocking && !d.resolved_at)
            .map(decisionCard),
          blocked,
          running: inScope(state.requests)
            .filter((r) => r.status === 'running')
            .map(requestCard),
          next: [],
        },
        generated_at: now(),
      }),
    })
  })

  // SSE event stream — flat: GET /api/v1/events?project_id=…
  await page.route(/\/api\/v1\/events(\?|$)/, (route: Route) => {
    const head = 'event: ready\ndata: {}\n\n'
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: head + state.sseEvents.join(''),
    })
  })

  // Conversation messages list — empty default (greenfield surfaces don't
  // render the chat-rail any more, but the legacy /api/v1/messages endpoint
  // still exists. Returning [] keeps the catch-all from leaking 200/{}.).
  await page.route(/\/api\/v1\/messages(\?|$)/, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  // Workspace files — empty tree default (G7.5c shape).
  await page.route(/\/api\/v1\/workspace-files\b.*/, (route: Route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/content')) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ path: '', entries: [] }),
    })
  })

  // Integrations admin — redacted defaults.
  await page.route('**/api/v1/integrations', (route: Route) => {
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
