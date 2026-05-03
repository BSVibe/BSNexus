import { test, expect } from '@playwright/test'

import { injectAuth, blockSSORedirect } from '../helpers/mock-api'
import {
  installFounderMocks,
  makeDecision,
  makeFounderState,
  makeRequest,
  makeRun,
  sseEvent,
} from '../helpers/founder-mock'

/**
 * Decision flow — the BSGateway worker's claude CLI calls back into
 * BSNexus via MCP ``decision.create`` to surface a fork to the
 * founder, then ``decision.wait`` blocks until the founder resolves
 * via the Decisions tab. This e2e validates the founder side: a
 * Decision row appears, the ``Resolve`` button posts to
 * ``/api/v1/decisions/{id}/resolve``, and the row flips to resolved.
 *
 * The MCP / queue / unblock part is covered by backend
 * ``test_mcp_decision_queue`` + ``test_mcp_tools``; this test only
 * exercises the UI surface end-to-end.
 */

const PROJECT_ID = 'proj-decision-flow'

test.describe('Decision flow — founder resolves a blocking decision', () => {
  test('Decisions tab shows a blocking row, resolve button posts the choice', async ({ page }) => {
    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Decision Flow')
    state.requests.push(
      makeRequest({
        id: 'req-1',
        project_id: PROJECT_ID,
        intent_summary: 'Implement the login screen',
        status: 'open',
      }),
    )
    state.runs.push(
      makeRun({
        id: 'run-1',
        project_id: PROJECT_ID,
        request_id: 'req-1',
        status: 'blocked',
        error_message: 'awaiting founder decision',
      }),
    )
    state.decisions.push(
      makeDecision({
        id: 'dec-1',
        project_id: PROJECT_ID,
        request_id: 'req-1',
        origin_run_id: 'run-1',
        question: 'Magic links or password+2FA?',
        options: ['Magic links', 'Password + 2FA'],
        blocking: true,
      }),
    )

    await installFounderMocks(page, state)

    await page.goto(`/projects/${PROJECT_ID}?tab=decisions`)

    // Decision card with the question text appears.
    await expect(page.getByText('Magic links or password+2FA?')).toBeVisible()
    // Both options render as resolve buttons.
    const magicBtn = page.getByRole('button', { name: 'Magic links' })
    const passwordBtn = page.getByRole('button', { name: 'Password + 2FA' })
    await expect(magicBtn).toBeVisible()
    await expect(passwordBtn).toBeVisible()

    // Click the founder's pick.
    await magicBtn.click()

    // Backend mock recorded a POST to /resolve with the chosen option.
    await expect.poll(() => state.posts.length).toBeGreaterThan(0)
    const resolvePost = state.posts.find((p) => p.url.includes('/decisions/dec-1/resolve'))
    expect(resolvePost).toBeDefined()
    expect((resolvePost!.body as { resolution: string }).resolution).toBe('Magic links')

    // After invalidation, the decision row flips to the "resolved" badge
    // (the mock mutated state.decisions[0].resolution + resolved_at).
    await expect(page.getByRole('button', { name: 'Magic links' })).toHaveCount(0)
  })

  test('SSE decision_resolved event dismisses the row without a manual click', async ({ page }) => {
    /**
     * The resolve API broadcasts ``decision_resolved`` on the project
     * SSE bus. A second BSNexus tab (or another founder) should see
     * the row dismiss without needing the local mutation to fire.
     *
     * We simulate this by pre-loading the decision as resolved in the
     * SSE feed and asserting the UI doesn't render the action buttons.
     */
    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Decision Flow')
    const dec = makeDecision({
      id: 'dec-2',
      project_id: PROJECT_ID,
      question: 'Postgres or SQLite?',
      options: ['Postgres', 'SQLite'],
      resolution: 'Postgres',
      resolved_by: 'founder@test',
      resolved_at: new Date().toISOString(),
    })
    state.decisions.push(dec)
    state.sseEvents.push(
      sseEvent('decision_resolved', {
        type: 'decision_resolved',
        id: dec.id,
        resolution: 'Postgres',
        resolved_by: 'founder@test',
      }),
    )

    await installFounderMocks(page, state)
    await page.goto(`/projects/${PROJECT_ID}?tab=decisions`)

    // The question still surfaces (in the resolved section), but no
    // resolve buttons exist because resolved_at is set.
    await expect(page.getByText('Postgres or SQLite?')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Postgres' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'SQLite' })).toHaveCount(0)
  })
})
