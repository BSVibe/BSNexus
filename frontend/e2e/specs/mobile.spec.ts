import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

/**
 * Phase B Batch 2 — mobile viewport smoke flow for BSNexus.
 *
 * Runs against the `pixel-5` (393×851) and `iphone-13` (390×844) Playwright
 * projects. The chromium desktop project still owns the deep regression
 * suite — this file focuses on the mobile-specific 3-column → 1-column
 * layout transformation and the canonical user flow (dashboard → drawer-nav
 * → founder-metaphor surfaces).
 *
 * Founder-metaphor surfaces all live inside `.mn`. Their content cards are
 * already vertically stacked (Direction = chat, Progress = timeline,
 * Decisions = list, Inside = tree) so the only structural mobile concern
 * is the off-canvas sidebar / chat-rail and the hamburger trigger.
 *
 * Preserves: design-tokens.ts SoT (no token changes), @tanstack/react-query
 * usage, the four founder-metaphor surfaces.
 */

test.describe('Mobile viewport: BSNexus core flow', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    if (testInfo.project.name === 'chromium') {
      testInfo.skip()
    }
    // Pin English locale — devcontainer default is Korean and the
    // assertions below match English link names ("Dashboard", etc.).
    await page.addInitScript(() => {
      localStorage.setItem('bsnexus.locale', 'en')
    })
    // Suppress the Next.js dev runtime-error overlay so it doesn't intercept
    // pointer events. The pre-existing GlobalChat `q.data.forEach` overlay
    // is unrelated to mobile chrome and is tracked separately.
    await page.addInitScript(() => {
      const css = document.createElement('style')
      css.textContent = 'nextjs-portal, [data-nextjs-toast], [data-nextjs-dialog-overlay] { display: none !important; }'
      document.head.appendChild(css)
    })
    await setupPage(page, '/settings')
  })

  test('settings page renders without horizontal overflow on mobile', async ({ page }) => {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(2)
  })

  test('hamburger toggle opens the sidebar drawer', async ({ page }) => {
    const hamburger = page.getByRole('button', { name: /open navigation/i })
    await expect(hamburger).toBeVisible()
    await hamburger.click()
    // Backdrop is now present (ships from `@bsvibe/layout`).
    await expect(page.getByTestId('bsvibe-sidebar-backdrop')).toBeVisible()
    // Sidebar is now visible — Dashboard nav link reachable.
    await expect(page.locator('aside').getByRole('link', { name: /Dashboard/ }).first()).toBeVisible()
  })

  test('hamburger trigger meets 44px touch-target minimum', async ({ page }) => {
    const hamburger = page.getByRole('button', { name: /open navigation/i })
    const box = await hamburger.boundingBox()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  })

  test('backdrop click closes the drawer', async ({ page }) => {
    await page.getByRole('button', { name: /open navigation/i }).click()
    const backdrop = page.getByTestId('bsvibe-sidebar-backdrop')
    await expect(backdrop).toBeVisible()
    await backdrop.click()
    await expect(backdrop).toHaveCount(0)
  })

  test('escape key closes the drawer', async ({ page }) => {
    await page.getByRole('button', { name: /open navigation/i }).click()
    await expect(page.getByTestId('bsvibe-sidebar-backdrop')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('bsvibe-sidebar-backdrop')).toHaveCount(0)
  })

  test('app uses single-column grid on mobile (sidebar off-canvas by default)', async ({ page }) => {
    // The .app container has grid-template-columns: 1fr at < 768px
    const cols = await page.locator('.app').first().evaluate((el) => {
      return window.getComputedStyle(el).gridTemplateColumns
    })
    // Single column ≈ a single track value (e.g. "393px").
    const tracks = cols.trim().split(/\s+/)
    expect(tracks.length).toBe(1)
  })
})
