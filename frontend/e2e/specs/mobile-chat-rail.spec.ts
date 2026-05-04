import { test, expect } from '@playwright/test'

import { injectAuth, blockSSORedirect } from '../helpers/mock-api'
import { installFounderMocks, makeFounderState } from '../helpers/founder-mock'

/**
 * Mobile chat-rail interaction — drawer-only on narrow viewports.
 *
 * Mirror spec to the desktop golden-path: tapping the FAB opens the
 * off-canvas chat drawer, the textarea receives focus, sending a
 * message produces a Request, and tapping the backdrop closes the
 * drawer.
 *
 * Skipped on desktop — `bsnexus-mobile-chat-fab` is hidden by CSS at
 * ≥768px (it's the `display: none` default rule overridden inside the
 * `@media (max-width: 767px)` block).
 */

const PROJECT_ID = 'proj-mobile-chat'

test.describe('Mobile chat-rail drawer', () => {
  test('FAB → drawer open → send message → backdrop closes', async ({
    page,
    viewport,
  }) => {
    test.skip(
      (viewport?.width ?? 0) >= 800,
      'Mobile chat FAB only renders on narrow viewports',
    )

    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Mobile Chat')
    await installFounderMocks(page, state)

    await page.goto(`/projects/${PROJECT_ID}`)

    // FAB is the only entry point on mobile — drawer starts closed.
    const fab = page.getByTestId('bsnexus-mobile-chat-fab')
    await expect(fab).toBeVisible({ timeout: 5000 })

    // Backdrop must NOT be present yet.
    await expect(page.getByTestId('bsnexus-mobile-backdrop')).toHaveCount(0)

    await fab.tap()

    // Drawer is now open: backdrop renders + chat textarea is in DOM.
    await expect(page.getByTestId('bsnexus-mobile-backdrop')).toBeVisible({
      timeout: 5000,
    })
    const textarea = page.locator('textarea').first()
    await textarea.waitFor({ state: 'visible', timeout: 5000 })
    await textarea.fill('Polish landing copy')
    await textarea.press('Enter')

    // Same inline-rule contract as desktop golden-path: non-empty
    // content ⇒ POST /messages + new Request + Run.
    await expect.poll(() => state.posts.length, { timeout: 5000 }).toBeGreaterThan(0)
    const sendPost = state.posts.find((p) =>
      p.url.endsWith(`/projects/${PROJECT_ID}/messages`),
    )
    expect(sendPost).toBeDefined()
    expect((sendPost!.body as { content: string }).content).toBe(
      'Polish landing copy',
    )
    expect(state.requests).toHaveLength(1)
    expect(state.runs).toHaveLength(1)

    // Tap backdrop → drawer closes; FAB re-appears.
    await page.getByTestId('bsnexus-mobile-backdrop').tap()
    await expect(page.getByTestId('bsnexus-mobile-backdrop')).toHaveCount(0)
    await expect(fab).toBeVisible()
  })

  test('FAB hidden on desktop viewport', async ({ page, viewport }) => {
    test.skip(
      (viewport?.width ?? 0) < 800,
      'Desktop-only — verifies the CSS hide rule on the FAB',
    )

    await blockSSORedirect(page)
    await injectAuth(page)

    const state = makeFounderState(PROJECT_ID, 'Mobile Chat')
    await installFounderMocks(page, state)

    await page.goto(`/projects/${PROJECT_ID}`)

    // FAB element may be in DOM but must be CSS-hidden on desktop.
    const fab = page.getByTestId('bsnexus-mobile-chat-fab')
    await expect(fab).toBeHidden()
  })
})
