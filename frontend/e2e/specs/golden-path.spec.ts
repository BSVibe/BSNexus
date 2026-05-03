import { test, expect } from '@playwright/test'

import { injectAuth, blockSSORedirect } from '../helpers/mock-api'
import {
  installFounderMocks,
  makeDeliverable,
  makeFounderState,
  makeRequest,
  makeRun,
  sseEvent,
} from '../helpers/founder-mock'

/**
 * Golden path — the M0 first deliverable: a Direction message becomes
 * a Request, BSGateway streams output back via the SSE ``run_output``
 * channel, the Inside panel renders that live, and a Deliverable lands
 * in the Progress tab.
 */

const PROJECT_ID = 'proj-golden'

test.describe('Golden path — Direction → live Inside output → Deliverable', () => {
  test('Inside panel renders streamed run_output chunks under the live badge', async ({
    page,
  }) => {
    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Golden Path')
    state.requests.push(
      makeRequest({
        id: 'req-1',
        project_id: PROJECT_ID,
        intent_summary: 'Fix the README typo',
      }),
    )
    state.runs.push(
      makeRun({
        id: 'run-1',
        project_id: PROJECT_ID,
        request_id: 'req-1',
        status: 'running',
        composition_snapshot_id: 'snap-001',
      }),
    )
    // SSE chunks: claude streams "Looking… ", "fixing…", "done." through
    // BSGateway → BSGatewayAdapter on_chunk → publish_run_output. The
    // Inspector accumulates them keyed by run_id.
    state.sseEvents.push(
      sseEvent('run_output', { type: 'run_output', run_id: 'run-1', content: 'Looking… ', finish_reason: null }),
      sseEvent('run_output', { type: 'run_output', run_id: 'run-1', content: 'fixing… ', finish_reason: null }),
      sseEvent('run_output', { type: 'run_output', run_id: 'run-1', content: 'done.', finish_reason: 'stop' }),
    )

    await installFounderMocks(page, state)
    await page.goto(`/projects/${PROJECT_ID}?tab=inspector&focusRequest=req-1`)

    // Inspector renders the Request row and selects run-1 by default.
    // The live badge surfaces because run.status === 'running' and the
    // run-output cache has accumulated text.
    await expect(page.getByText('Fix the README typo').first()).toBeVisible()
    await expect(page.getByText('● live')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('Looking… fixing… done.')).toBeVisible({ timeout: 5000 })
  })

  test('Progress tab shows the deliverable produced by the run', async ({ page }) => {
    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Golden Path')
    state.requests.push(
      makeRequest({
        id: 'req-2',
        project_id: PROJECT_ID,
        intent_summary: 'Add a contributing guide',
      }),
    )
    state.runs.push(
      makeRun({
        id: 'run-2',
        project_id: PROJECT_ID,
        request_id: 'req-2',
        status: 'done',
      }),
    )
    state.deliverables.push(
      makeDeliverable({
        id: 'del-1',
        project_id: PROJECT_ID,
        request_id: 'req-2',
        title: 'CONTRIBUTING.md',
        type: 'doc',
        status: 'ready',
      }),
    )

    await installFounderMocks(page, state)
    await page.goto(`/projects/${PROJECT_ID}?tab=progress`)

    // The Deliverable timeline lists the document.
    await expect(page.getByText('CONTRIBUTING.md')).toBeVisible({ timeout: 5000 })
  })

  test('Sending a chat message creates a Request and seeds a Run', async ({ page, viewport }) => {
    // GlobalChat rail is collapsed by default on narrow viewports
    // (`@bsvibe/layout` ResponsiveSidebar drawer pattern). We exercise
    // the inline-rule contract on the desktop chrome only — the mobile
    // chat-drawer interaction is a separate UX concern and gets its
    // own spec when we add mobile chat regression coverage.
    test.skip(
      (viewport?.width ?? 0) < 800,
      'GlobalChat rail is drawer-only on mobile; desktop covers the API contract',
    )

    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Golden Path')
    await installFounderMocks(page, state)

    // Force the chat rail expanded before the page mounts so the
    // textarea is in the DOM. ``CHAT_COLLAPSED_KEY`` is ``"chat-collapsed"``
    // per `Layout.tsx`; setting it to ``"0"`` keeps the rail open.
    await page.addInitScript(() => {
      localStorage.setItem('chat-collapsed', '0')
    })

    await page.goto(`/projects/${PROJECT_ID}`)

    const textarea = page.locator('textarea').first()
    await textarea.waitFor({ state: 'visible', timeout: 10_000 })
    await textarea.fill('Add a contributing guide')
    await textarea.press('Enter')

    await expect.poll(() => state.posts.length, { timeout: 5000 }).toBeGreaterThan(0)
    const sendPost = state.posts.find((p) => p.url.endsWith(`/projects/${PROJECT_ID}/messages`))
    expect(sendPost).toBeDefined()
    expect((sendPost!.body as { content: string }).content).toBe('Add a contributing guide')
    // The mock-side rule mirrors backend: non-empty content ⇒ new Request + Run.
    expect(state.requests).toHaveLength(1)
    expect(state.runs).toHaveLength(1)
  })
})
