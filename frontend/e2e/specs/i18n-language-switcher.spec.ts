import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

/**
 * Phase C — BSNexus i18n smoke for next-intl + language switcher.
 *
 * The Settings page exposes a Language section; flipping it persists
 * the choice in localStorage and re-renders surfaces with the matching
 * messages bundle. ko/en parity is enforced separately by
 * ``scripts/verify-i18n.mjs``; this spec covers the runtime behavior.
 *
 * Why mobile projects skip: the existing mobile suite intentionally
 * runs on pixel-5 / iphone-13 only (chromium project skips). Adding
 * a duplicate viewport split here would hide the i18n behavior under
 * the mobile drawer mechanics that ``mobile.spec.ts`` already covers.
 */
test.describe('i18n language switcher (next-intl)', () => {
  test.beforeEach(async ({ page: _page }, testInfo) => {
    if (testInfo.project.name !== 'chromium') {
      testInfo.skip()
    }
  })

  test('Settings page renders the Language switcher', async ({ page }) => {
    await setupPage(page, '/settings?section=language')
    await expect(page.getByTestId('language-switcher')).toBeVisible()
  })

  test('switching to English updates Sidebar workspace labels', async ({ page }) => {
    await setupPage(page, '/settings?section=language')
    await page.getByTestId('language-switcher-en').click()
    // Sidebar uses the layout namespace — Dashboard / Settings labels
    // must reflect the English bundle once the switcher commits.
    await expect(page.locator('aside').first().getByText('Dashboard')).toBeVisible()
    await expect(page.locator('aside').first().getByText('Settings')).toBeVisible()
  })

  test('switching to Korean persists across navigation', async ({ page }) => {
    await setupPage(page, '/settings?section=language')
    await page.getByTestId('language-switcher-ko').click()
    // Sidebar Workspace section header is translated.
    await expect(page.locator('aside').first().getByText('워크스페이스')).toBeVisible()
    // Reload — locale must survive a refresh because we persist to
    // localStorage (no [locale] segment in the route, by design).
    await page.reload({ waitUntil: 'networkidle' })
    await expect(page.locator('aside').first().getByText('워크스페이스')).toBeVisible()
  })
})
