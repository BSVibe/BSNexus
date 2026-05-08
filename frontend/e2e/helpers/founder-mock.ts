/**
 * Mock helper for the founder-metaphor surface (post Direction reset
 * 2026-05-03): conversation messages, decisions, deliverables, requests,
 * runs, and the per-project SSE event stream.
 *
 * The SSE stream is stubbed by emitting all events as one body. The
 * browser's EventSource dispatches them sequentially as it parses, so
 * tests can verify the final UI state after the events land.
 */

import type { Page, Route } from '@playwright/test'

export interface MockMessage {
  id: string
  project_id: string
  role: 'user' | 'assistant'
  content: string
  request_id: string | null
  actions?: Array<Record<string, unknown>>
  created_at: string
}

export interface MockRequest {
  id: string
  tenant_id: string
  project_id: string
  origin_message_id: string | null
  intent_summary: string
  status: 'open' | 'completed' | 'cancelled'
  originator_auth?: string | null
  created_at: string
  updated_at: string
}

export type MockReplyQualityKind =
  | 'real_tool_calls'
  | 'pseudocode_in_chat'
  | 'fenced_block_only'
  | 'empty'
  | 'mixed'

export interface MockRunSummary {
  total_rounds: number
  total_tool_calls: number
  per_round: Array<{
    round_idx: number
    content_chars: number
    tool_call_count: number
    reply_quality: MockReplyQualityKind
    finish_reason: string | null
  }>
  dominant_reply_quality: MockReplyQualityKind
  did_emit_fenced_block: boolean
  files_actually_written: string[]
  failure_signals: string[]
}

export interface MockRunActivity {
  id: string
  run_id: string
  project_id: string
  level: 'milestone' | 'tool'
  event_type: string
  summary: string
  detail: Record<string, unknown> | null
  created_at: string
}

export interface MockExecutionRun {
  id: string
  tenant_id: string
  project_id: string
  request_id: string | null
  parent_run_id: string | null
  status: 'pending' | 'running' | 'blocked' | 'done'
  priority: 'low' | 'medium' | 'high'
  composition_snapshot_id: string | null
  output_type: string | null
  output_ref: Record<string, unknown> | null
  estimated_cost_cents: number
  actual_cost_cents: number
  branch_name: string | null
  commit_hash: string | null
  // worker_id retired with the workers table; field absent.
  error_message: string | null
  retry_count: number
  max_retries: number
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
  run_summary: MockRunSummary | null
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
}

const TENANT = 'tenant-001'

export function makeMessage(p: Partial<MockMessage> & { id: string; project_id: string }): MockMessage {
  return {
    id: p.id,
    project_id: p.project_id,
    role: p.role ?? 'user',
    content: p.content ?? '',
    request_id: p.request_id ?? null,
    actions: p.actions ?? [],
    created_at: p.created_at ?? new Date().toISOString(),
  }
}

export function makeRequest(p: Partial<MockRequest> & { id: string; project_id: string }): MockRequest {
  return {
    id: p.id,
    tenant_id: p.tenant_id ?? TENANT,
    project_id: p.project_id,
    origin_message_id: p.origin_message_id ?? null,
    intent_summary: p.intent_summary ?? 'do something',
    status: p.status ?? 'open',
    originator_auth: p.originator_auth ?? null,
    created_at: p.created_at ?? new Date().toISOString(),
    updated_at: p.updated_at ?? new Date().toISOString(),
  }
}

export function makeRun(p: Partial<MockExecutionRun> & { id: string; project_id: string }): MockExecutionRun {
  return {
    id: p.id,
    tenant_id: p.tenant_id ?? TENANT,
    project_id: p.project_id,
    request_id: p.request_id ?? null,
    parent_run_id: p.parent_run_id ?? null,
    status: p.status ?? 'pending',
    priority: p.priority ?? 'medium',
    composition_snapshot_id: p.composition_snapshot_id ?? null,
    output_type: p.output_type ?? null,
    output_ref: p.output_ref ?? null,
    estimated_cost_cents: p.estimated_cost_cents ?? 0,
    actual_cost_cents: p.actual_cost_cents ?? 0,
    branch_name: p.branch_name ?? null,
    commit_hash: p.commit_hash ?? null,
    error_message: p.error_message ?? null,
    retry_count: p.retry_count ?? 0,
    max_retries: p.max_retries ?? 3,
    created_at: p.created_at ?? new Date().toISOString(),
    updated_at: p.updated_at ?? new Date().toISOString(),
    started_at: p.started_at ?? null,
    completed_at: p.completed_at ?? null,
    run_summary: p.run_summary ?? null,
  }
}

export function makeDecision(p: Partial<MockDecision> & { id: string; project_id: string; question: string }): MockDecision {
  return {
    id: p.id,
    tenant_id: p.tenant_id ?? TENANT,
    project_id: p.project_id,
    request_id: p.request_id ?? null,
    origin_run_id: p.origin_run_id ?? null,
    question: p.question,
    options: p.options ?? [],
    blocking: p.blocking ?? true,
    resolution: p.resolution ?? null,
    resolved_by: p.resolved_by ?? null,
    resolved_at: p.resolved_at ?? null,
    created_at: p.created_at ?? new Date().toISOString(),
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
    type: p.type ?? 'doc',
    title: p.title,
    status: p.status ?? 'ready',
    current_version_id: p.current_version_id ?? null,
    created_at: p.created_at ?? new Date().toISOString(),
    updated_at: p.updated_at ?? new Date().toISOString(),
  }
}

// ─── State container ─────────────────────────────────────────────────

/**
 * Per-test state: mutated by the route handlers below. Tests construct
 * one of these, configure it, then call ``installFounderMocks(page, state)``
 * to attach the route handlers. State mutations between requests show
 * up because Playwright re-invokes the handler on every route.
 */
export interface FounderMockState {
  projectId: string
  projectName?: string
  messages: MockMessage[]
  requests: MockRequest[]
  runs: MockExecutionRun[]
  decisions: MockDecision[]
  deliverables: MockDeliverable[]
  /** Per-run activity rows, keyed by run_id (PR7 — Inside-panel
   * RunActivityTimeline reads ``/api/v1/runs/{id}/activities``). */
  activities: MockRunActivity[]
  /** Pre-recorded SSE event blocks delivered when the client opens
   * ``/api/v1/projects/{id}/events``. Each entry is one ``event:``+``data:``
   * pair, joined with the SSE separator. */
  sseEvents: string[]
  /** Captured: appended to whenever resolve or send POST handlers fire. */
  posts: { url: string; body: unknown }[]
}

export function makeFounderState(projectId: string, projectName = 'Test Project'): FounderMockState {
  return {
    projectId,
    projectName,
    messages: [],
    requests: [],
    runs: [],
    decisions: [],
    deliverables: [],
    activities: [],
    sseEvents: [],
    posts: [],
  }
}

/** Build a single SSE event block (`event:`/`data:` pair). */
export function sseEvent(eventType: string, payload: Record<string, unknown>): string {
  return `event: ${eventType}\ndata: ${JSON.stringify(payload)}\n\n`
}

// ─── Route installer ─────────────────────────────────────────────────

export async function installFounderMocks(page: Page, state: FounderMockState): Promise<void> {
  const pid = state.projectId

  const projectShape = () => ({
    id: pid,
    tenant_id: TENANT,
    name: state.projectName ?? 'Test Project',
    description: null,
    status: 'active',
    workspace_type: 'local_managed',
    github_repo_url: null,
    github_branch: null,
    repo_path: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  })

  // List projects — must include this state's project so GlobalChat's
  // `useQuery(['projects'])` finds the current project. Without this,
  // ``currentProject`` resolves to null and the chat textarea silently
  // does nothing on Enter (mutation never fires).
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

  // Single project metadata
  await page.route(`**/api/v1/projects/${pid}`, (route: Route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ status: 204, body: '' })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: pid,
        tenant_id: TENANT,
        name: state.projectName ?? 'Test Project',
        description: null,
        status: 'active',
        workspace_type: 'local_managed',
        github_repo_url: null,
        github_branch: null,
        repo_path: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }),
    })
  })

  // Conversation messages — list + send
  await page.route(`**/api/v1/projects/${pid}/messages`, async (route: Route) => {
    const method = route.request().method()
    if (method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(state.messages),
      })
    }
    if (method === 'POST') {
      const body = JSON.parse(route.request().postData() ?? '{}') as { content: string }
      state.posts.push({ url: route.request().url(), body })

      const messageId = `msg-${state.messages.length + 1}`
      const userMsg = makeMessage({
        id: messageId,
        project_id: pid,
        role: 'user',
        content: body.content,
      })

      const stripped = (body.content ?? '').trim()
      let requestObj: MockRequest | null = null
      let createdNew = false
      if (stripped) {
        const reqId = `req-${state.requests.length + 1}`
        requestObj = makeRequest({
          id: reqId,
          project_id: pid,
          origin_message_id: messageId,
          intent_summary: stripped.slice(0, 240),
        })
        state.requests.push(requestObj)
        userMsg.request_id = reqId
        createdNew = true

        const ackMsg = makeMessage({
          id: `${messageId}-ack`,
          project_id: pid,
          role: 'assistant',
          content: `⚡ Starting work on: **${stripped}**`,
          request_id: reqId,
          actions: [{ kind: 'ack', run_id: `run-${state.runs.length + 1}` }],
        })
        state.messages.push(userMsg, ackMsg)

        // Seed a pending run.
        state.runs.push(
          makeRun({
            id: `run-${state.runs.length + 1}`,
            project_id: pid,
            request_id: reqId,
            status: 'pending',
          }),
        )
      } else {
        state.messages.push(userMsg)
      }

      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          message: userMsg,
          intent: createdNew ? 'request' : 'chit_chat',
          request_id: requestObj?.id ?? null,
          request_created: createdNew,
          intent_summary: requestObj?.intent_summary ?? null,
        }),
      })
    }
    return route.continue()
  })

  // Requests for a project
  await page.route(`**/api/v1/projects/${pid}/requests`, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.requests),
    })
  })

  // Runs for a request — match any request id
  await page.route(`**/api/v1/requests/*/runs`, (route: Route) => {
    const url = route.request().url()
    const m = /\/requests\/([^/]+)\/runs/.exec(url)
    const reqId = m?.[1] ?? null
    const runs = reqId === null ? state.runs : state.runs.filter((r) => r.request_id === reqId)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runs),
    })
  })

  // Composition snapshots — minimal stub
  await page.route(`**/api/v1/composition-snapshots/*`, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'snap-001',
        run_id: null,
        request_id: null,
        source: 'local',
        persona_label: 'Generalist',
        fit_score: 0.5,
        system_prompt_ref: { inline: 'You are a careful engineer.' },
        tools_allowed: ['file_read', 'file_write'],
        context_doc_refs: [],
        created_at: new Date().toISOString(),
      }),
    })
  })

  // Deliverables
  await page.route(`**/api/v1/projects/${pid}/deliverables`, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.deliverables),
    })
  })

  // Decisions
  await page.route(`**/api/v1/projects/${pid}/decisions`, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.decisions),
    })
  })

  // Decision resolve
  await page.route(`**/api/v1/decisions/*/resolve`, async (route: Route) => {
    const url = route.request().url()
    const m = /\/decisions\/([^/]+)\/resolve/.exec(url)
    const decId = m?.[1] ?? ''
    const body = JSON.parse(route.request().postData() ?? '{}') as {
      resolution: string
      resolved_by?: string | null
    }
    state.posts.push({ url, body })
    const decision = state.decisions.find((d) => d.id === decId)
    if (!decision) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
    }
    decision.resolution = body.resolution
    decision.resolved_by = body.resolved_by ?? 'founder@test'
    decision.resolved_at = new Date().toISOString()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(decision),
    })
  })

  // SSE events — emit the pre-recorded sequence as one body.
  await page.route(`**/api/v1/projects/${pid}/events*`, (route: Route) => {
    const head = `retry: 3000\n\nevent: ready\ndata: ${JSON.stringify({ project_id: pid })}\n\n`
    const body = head + state.sseEvents.join('')
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: {
        'Cache-Control': 'no-cache',
      },
      body,
    })
  })

  // Workspace files (used by FilesView, may be invoked when the page mounts)
  await page.route(`**/api/v1/projects/${pid}/workspace-files**`, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  // Integrations (used by ProgressView trust cards)
  await page.route(`**/api/v1/integrations`, (route: Route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        bsage: { provider: 'bsage', enabled: false, has_api_key: false, base_url: null, extra_config: {} },
        bsupervisor: { provider: 'bsupervisor', enabled: false, has_api_key: false, base_url: null, extra_config: {} },
      }),
    })
  })

  // ─── A3 flat-REST routes (PR2 / 2026-05-08) ─────────────────────────
  // ``requestsApi.listForProject(pid)`` hits ``/api/v1/requests?project_id=pid``.
  await page.route(/\/api\/v1\/requests(\?|$)/, (route: Route) => {
    const url = new URL(route.request().url())
    const pidParam = url.searchParams.get('project_id')
    const filtered = pidParam ? state.requests.filter((r) => r.project_id === pidParam) : state.requests
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(filtered),
    })
  })

  // ``requestsApi.listRuns(reqId)`` hits ``/api/v1/runs?request_id=reqId``.
  // ``listActivities(runId)`` hits ``/api/v1/runs/{id}/activities``.
  await page.route(/\/api\/v1\/runs(\?|$)/, (route: Route) => {
    const url = new URL(route.request().url())
    const reqId = url.searchParams.get('request_id')
    const runs = reqId ? state.runs.filter((r) => r.request_id === reqId) : state.runs
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runs),
    })
  })
  await page.route(/\/api\/v1\/runs\/[^/]+\/activities/, (route: Route) => {
    const url = new URL(route.request().url())
    const m = /\/runs\/([^/]+)\/activities/.exec(url.pathname)
    const runId = m?.[1] ?? ''
    const level = url.searchParams.get('level')
    let rows = state.activities.filter((a) => a.run_id === runId)
    if (level) rows = rows.filter((a) => a.level === level)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(rows),
    })
  })

  // PR7 — run-summaries surface (failure-mode dashboard backend).
  await page.route(/\/api\/v1\/run-summaries(\?|$)/, (route: Route) => {
    const url = new URL(route.request().url())
    const isAggregate = url.searchParams.get('aggregate') === 'true'
    const pidParam = url.searchParams.get('project_id')
    const runs = pidParam ? state.runs.filter((r) => r.project_id === pidParam) : state.runs
    if (isAggregate) {
      const counts: Record<string, number> = {}
      for (const r of runs) {
        const kind = r.run_summary?.dominant_reply_quality
        if (kind) counts[kind] = (counts[kind] ?? 0) + 1
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window_days: Number(url.searchParams.get('days') ?? 7),
          total_runs: runs.length,
          counts,
          project_id: pidParam,
        }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        runs.map((r) => ({
          run_id: r.id,
          project_id: r.project_id,
          status: r.status,
          created_at: r.created_at,
          completed_at: r.completed_at,
          summary: r.run_summary,
        })),
      ),
    })
  })
}
