import { test, expect } from '@playwright/test'

import { injectAuth, blockSSORedirect } from '../helpers/mock-api'
import {
  installFounderMocks,
  makeDecision,
  makeDeliverable,
  makeFounderState,
  makeRequest,
} from '../helpers/founder-mock'

/**
 * ResponsiveTable adoption — the decisions / brief / dashboard views
 * render the shared `@bsvibe/ui` `<ResponsiveTable>`. The component
 * dual-renders the SAME row data into BOTH the DOM trees:
 *   - `bsvibe-table-scroll` → `<table>`, visible at the `sm:` (640px)
 *     breakpoint and up, `display:none` below it.
 *   - `bsvibe-table-mobile` → card stack, visible below `sm:`.
 *
 * Because both trees are always in the DOM, text assertions are scoped
 * to the tree expected to be VISIBLE at the active viewport:
 *   - chromium (1280px)  → table visible, mobile stack hidden.
 *   - pixel-5 / iphone-13 (< 640px) → mobile stack visible, table hidden.
 */

const DECISIONS_PROJECT = 'proj-rt-decisions'
const BRIEF_PROJECT = 'proj-rt-brief'
const DASHBOARD_PROJECT = 'proj-rt-dashboard'

function isMobile(width: number | undefined): boolean {
  // The shared component switches at Tailwind's `sm:` (640px).
  return (width ?? 0) < 640
}

/**
 * Asserts the ResponsiveTable rendered the right tree for the viewport
 * and that `text` is reachable inside the visible tree.
 */
async function expectResponsiveTable(
  page: import('@playwright/test').Page,
  viewportWidth: number | undefined,
  text: string,
): Promise<void> {
  // A page may host several ResponsiveTables (the Brief work tab has a
  // Running section + a Shipped section). Scope to the table tree that
  // actually contains `text` rather than assuming the first one.
  if (isMobile(viewportWidth)) {
    const mobileStack = page
      .getByTestId('bsvibe-table-mobile')
      .filter({ hasText: text })
      .first()
    await expect(mobileStack).toBeVisible()
    await expect(mobileStack.getByText(text).first()).toBeVisible()
    // The desktop table tree is in the DOM but hidden below `sm:`.
    await expect(page.getByTestId('bsvibe-table-scroll').first()).toBeHidden()
  } else {
    const tableScroll = page
      .getByTestId('bsvibe-table-scroll')
      .filter({ hasText: text })
      .first()
    await expect(tableScroll).toBeVisible()
    await expect(tableScroll.locator('table')).toBeVisible()
    await expect(tableScroll.getByText(text).first()).toBeVisible()
  }
}

test.describe('ResponsiveTable — DecisionsView', () => {
  test('decisions render through ResponsiveTable (table on desktop, cards on mobile)', async ({
    page,
    viewport,
  }) => {
    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(DECISIONS_PROJECT, 'RT Decisions')
    state.decisions.push(
      makeDecision({
        id: 'dec-rt-1',
        project_id: DECISIONS_PROJECT,
        question: 'Magic links or password+2FA?',
        options: ['Magic links', 'Password + 2FA'],
        blocking: true,
      }),
    )

    await installFounderMocks(page, state)
    await page.goto(`/projects/${DECISIONS_PROJECT}?tab=decisions`)

    // Wait for the decision data to land in whichever tree.
    await expect(page.getByText('Magic links or password+2FA?').first()).toBeAttached({
      timeout: 10_000,
    })
    await expectResponsiveTable(page, viewport?.width, 'Magic links or password+2FA?')
  })
})

test.describe('ResponsiveTable — Brief work tab', () => {
  test('shipped/running sections render through ResponsiveTable', async ({ page, viewport }) => {
    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(BRIEF_PROJECT, 'RT Brief')
    state.requests.push(
      makeRequest({
        id: 'req-rt-1',
        project_id: BRIEF_PROJECT,
        intent_summary: 'Wire the billing webhook',
        status: 'running',
      }),
    )
    state.deliverables.push(
      makeDeliverable({
        id: 'del-rt-1',
        project_id: BRIEF_PROJECT,
        request_id: 'req-rt-1',
        title: 'WEBHOOK.md',
        type: 'doc',
        status: 'delivered',
        proof_state: 'verified',
        verifier_type: 'doc_lint',
        proof_summary: 'doc lint OK',
        verification_exit_code: 0,
      }),
    )

    await installFounderMocks(page, state)
    await page.goto(`/projects/${BRIEF_PROJECT}?tab=work`)

    await expect(page.getByText('WEBHOOK.md').first()).toBeAttached({ timeout: 10_000 })
    await expectResponsiveTable(page, viewport?.width, 'WEBHOOK.md')
  })
})

test.describe('ResponsiveTable — Dashboard project collection', () => {
  test('projects render through ResponsiveTable (table on desktop, cards on mobile)', async ({
    page,
    viewport,
  }) => {
    await blockSSORedirect(page)
    await injectAuth(page)

    // installFounderMocks serves a single project on /api/v1/projects;
    // that's enough to exercise the dashboard table dual-render.
    const state = makeFounderState(DASHBOARD_PROJECT, 'RT Dashboard')
    await installFounderMocks(page, state)
    await page.goto('/dashboard')

    await expect(page.getByText('RT Dashboard').first()).toBeAttached({ timeout: 10_000 })
    await expectResponsiveTable(page, viewport?.width, 'RT Dashboard')
  })
})
