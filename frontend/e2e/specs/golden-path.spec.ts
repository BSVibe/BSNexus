import { test, expect } from '@playwright/test'

import { injectAuth, blockSSORedirect } from '../helpers/mock-api'
import {
  installFounderMocks,
  makeDeliverable,
  makeFounderState,
  makeRequest,
} from '../helpers/founder-mock'

/**
 * Golden path (greenfield) — Direction → Request → Deliverable.
 *
 * The founder posts a directive via the Direction primitive
 * (``POST /api/v1/directions``); the server opens a Request; the
 * verifier worker stamps a proof state on the resulting Deliverable;
 * the Shipped tab on the project page surfaces the verified output.
 *
 * The legacy chat-rail / Inside-panel run-output streaming surface
 * retired with the file-disposition.md greenfield purge — the new
 * golden path is driven entirely by the Direction → Brief flow.
 */

const PROJECT_ID = 'proj-golden'

test.describe('Golden path — Direction → Brief deliverable', () => {
  test('Shipped tab shows the verified deliverable', async ({ page }) => {
    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Golden Path')
    state.requests.push(
      makeRequest({
        id: 'req-1',
        project_id: PROJECT_ID,
        intent_summary: 'Add a contributing guide',
      }),
    )
    state.deliverables.push(
      makeDeliverable({
        id: 'del-1',
        project_id: PROJECT_ID,
        request_id: 'req-1',
        title: 'CONTRIBUTING.md',
        type: 'doc',
        status: 'delivered',
        proof_state: 'verified',
        verifier_type: 'doc_lint',
        proof_summary: 'doc lint OK',
        verification_exit_code: 0,
      }),
    )

    await installFounderMocks(page, state)
    await page.goto(`/projects/${PROJECT_ID}?tab=work`)

    // Work tab renders the verified deliverable card under the Shipped section.
    await expect(page.getByText('CONTRIBUTING.md')).toBeVisible({ timeout: 10_000 })
  })

  test('DirectionInputCard on the Home tab posts to /api/v1/directions', async ({
    page,
    viewport,
  }) => {
    // The card lives at the top of the Home tab on every viewport. We
    // exercise the desktop chrome contract; mobile is covered separately
    // by mobile-founder-flow.spec.ts.
    test.skip(
      (viewport?.width ?? 0) < 800,
      'Project DirectionInputCard mobile contract lives in mobile-founder-flow.spec.ts',
    )

    await blockSSORedirect(page)
    await injectAuth(page)
    // Pin English locale — devcontainer default is Korean and the
    // submit button label assertion below is English-only.
    await page.addInitScript(() => {
      localStorage.setItem('bsnexus.locale', 'en')
    })

    const state = makeFounderState(PROJECT_ID, 'Golden Path')
    await installFounderMocks(page, state)
    await page.goto(`/projects/${PROJECT_ID}?tab=home`)

    const card = page.getByTestId('direction-input-card')
    await expect(card).toBeVisible({ timeout: 10_000 })

    const textarea = card.getByRole('textbox')
    await textarea.fill('Add a contributing guide')
    await card.getByRole('button', { name: /direct|send|submit/i }).click()

    await expect.poll(() => state.posts.length, { timeout: 5000 }).toBeGreaterThan(0)
    const post = state.posts.find((p) => p.url.includes('/api/v1/directions'))
    expect(post).toBeDefined()
    const body = post!.body as { body?: string; project_id?: string | null }
    expect(body.body).toBe('Add a contributing guide')
    // Bound to the active project on the project page surface.
    expect(body.project_id).toBe(PROJECT_ID)
  })
})
