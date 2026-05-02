import { test, expect } from '@playwright/test'
import { mockAllApis, injectAuth, blockSSORedirect } from '../helpers/mock-api'

test.describe('Auth — Landing Page & Protected Routes', () => {
  test('landing page shows BSNexus branding and sign-in button', async ({ page }) => {
    await blockSSORedirect(page)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'BSNexus' })).toBeVisible()
    await expect(page.getByText(/Orchestrate AI agents|AI 에이전트/)).toBeVisible()
    await expect(page.getByRole('button', { name: /Sign in with BSVibe|BSVibe로 로그인/ })).toBeVisible()
  })

  test('landing page does not probe the remote auth session before sign-in', async ({ page }) => {
    const sessionRequests: string[] = []
    await page.route('**/api/session', (route) => {
      sessionRequests.push(route.request().url())
      return route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'no session' }),
      })
    })

    await page.goto('/')
    await expect(page.getByRole('button', { name: /Sign in with BSVibe|BSVibe로 로그인/ })).toBeVisible()
    expect(sessionRequests).toEqual([])
  })

  test('landing page remains usable on a phone viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')

    const cta = page.getByRole('button', { name: /Sign in with BSVibe|BSVibe로 로그인/ })
    const heading = page.getByRole('heading', { name: 'BSNexus' })
    await expect(cta).toBeVisible()
    const metrics = await page.evaluate(() => {
      return {
        viewportWidth: window.innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }
    })
    const ctaBox = await cta.boundingBox()
    const headingBox = await heading.boundingBox()
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.viewportWidth)
    expect(ctaBox?.width ?? 0).toBeGreaterThan(300)
    expect(headingBox?.width ?? 0).toBeLessThan(metrics.viewportWidth)
  })

  test('landing page displays three feature cards with Material Symbols', async ({ page }) => {
    await blockSSORedirect(page)
    await page.goto('/')
    await expect(page.getByText(/Conversational Planning|대화형 플래닝/)).toBeVisible()
    await expect(page.getByText(/Live Plan View|실시간 진행/)).toBeVisible()
    await expect(page.getByText(/Distributed Workers|분산 워커/)).toBeVisible()
    // Material Symbols icons are present
    await expect(page.locator('span.material-symbols-outlined:has-text("psychology")')).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("account_tree")')).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("hub")')).toBeVisible()
  })

  test('landing page shows "Go to Dashboard" when authenticated', async ({ page }) => {
    await injectAuth(page)
    await page.goto('/')
    await expect(page.getByRole('button', { name: /Go to Dashboard|대시보드/ })).toBeVisible()
  })

  test('unauthenticated user is redirected to landing from /dashboard', async ({ page }) => {
    await blockSSORedirect(page)
    await mockAllApis(page)
    await page.goto('/dashboard')
    // ProtectedRoute should redirect to /
    await expect(page).toHaveURL('/')
  })

  test('landing page footer shows "Powered by BSVibe"', async ({ page }) => {
    await blockSSORedirect(page)
    await page.goto('/')
    await expect(page.getByText('Powered by BSVibe')).toBeVisible()
  })
})
