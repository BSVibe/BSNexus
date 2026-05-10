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
 * Decision flow — when a run blocks on a fork, BSNexus surfaces the
 * decision in the founder Decisions tab. This e2e validates the
 * founder-side UI against the flat A3 endpoints:
 *   - A blocking decision row appears
 *   - Tapping an option posts to ``/api/v1/decisions/{id}/resolve``
 *   - Resolution flips the row to the resolved state
 *
 * The MCP / queue / unblock side is covered by backend tests.
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

    await expect(page.getByText('Magic links or password+2FA?')).toBeVisible({ timeout: 10_000 })
    const magicBtn = page.getByRole('button', { name: 'Magic links' })
    const passwordBtn = page.getByRole('button', { name: 'Password + 2FA' })
    await expect(magicBtn).toBeVisible()
    await expect(passwordBtn).toBeVisible()

    await magicBtn.click()

    await expect.poll(() => state.posts.length, { timeout: 5000 }).toBeGreaterThan(0)
    const resolvePost = state.posts.find((p) => p.url.includes('/decisions/dec-1/resolve'))
    expect(resolvePost).toBeDefined()
    expect((resolvePost!.body as { resolution: string }).resolution).toBe('Magic links')

    // The card flips to resolved — option buttons disappear.
    await expect(page.getByRole('button', { name: 'Magic links' })).toHaveCount(0)
  })

  test('SSE decision_resolved event renders the row in resolved state', async ({ page }) => {
    /**
     * The resolve API broadcasts ``decision_resolved`` on the project
     * SSE bus. A second BSNexus tab should see the row in the resolved
     * section without needing a manual click — pre-loading the
     * decision as resolved in the SSE feed mirrors that.
     */
    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Decision Flow')
    state.decisions.push(
      makeDecision({
        id: 'dec-2',
        project_id: PROJECT_ID,
        question: 'Postgres or SQLite?',
        options: ['Postgres', 'SQLite'],
        resolution: 'Postgres',
        resolved_by: 'founder@test',
        resolved_at: new Date().toISOString(),
      }),
    )
    state.sseEvents.push(
      sseEvent('decision_resolved', {
        type: 'decision_resolved',
        id: 'dec-2',
        resolution: 'Postgres',
        resolved_by: 'founder@test',
      }),
    )

    await installFounderMocks(page, state)
    await page.goto(`/projects/${PROJECT_ID}?tab=decisions`)

    await expect(page.getByText('Postgres or SQLite?')).toBeVisible({ timeout: 10_000 })
    // No resolve buttons because resolved_at is set.
    await expect(page.getByRole('button', { name: 'Postgres' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'SQLite' })).toHaveCount(0)
  })
})
