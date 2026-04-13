/**
 * Live E2E login helper — browser-based BSVibe SSO.
 *
 * 1. Navigate to BSNexus on bsserver (Tailscale hostname)
 * 2. Click "Sign in with BSVibe" → redirects to auth.bsvibe.dev
 * 3. Fill email/password → submit
 * 4. Auth server redirects back with #access_token in hash
 * 5. BSNexus frontend picks up token → authenticated
 */
import type { Page } from '@playwright/test'

const LIVE_FRONTEND_URL = process.env.LIVE_FRONTEND_URL || 'http://bsserver:13100'
export const LIVE_API_URL = process.env.LIVE_API_URL || 'http://bsserver:18100'
export const skipUnlessLive = false

const E2E_EMAIL = process.env.E2E_EMAIL || 'admin@bsvibe.dev'
const E2E_PASSWORD = process.env.E2E_PASSWORD || 'admin1234!'

export async function loginAndNavigate(page: Page, path: string): Promise<void> {
  await page.goto(`${LIVE_FRONTEND_URL}${path}`, { waitUntil: 'networkidle', timeout: 15_000 })

  // Click "Sign in with BSVibe" on landing page
  const ssoBtn = page.getByText(/sign in with bsvibe/i).first()
  if (await ssoBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await ssoBtn.click()
    // Wait for auth.bsvibe.dev login page
    await page.waitForURL('**/login**', { timeout: 10_000 }).catch(() => {})
  }

  // Fill BSVibe Auth login form
  const emailField = page.locator('input[type="email"], input[placeholder*="example"]').first()
  if (await emailField.isVisible({ timeout: 5_000 }).catch(() => false)) {
    await emailField.fill(E2E_EMAIL)
    await page.locator('input[type="password"]').first().fill(E2E_PASSWORD)

    await page.getByRole('button', { name: /sign in/i }).click()

    // Wait for redirect back to BSNexus with hash tokens
    await page.waitForURL(`${LIVE_FRONTEND_URL}/**`, { timeout: 20_000 }).catch(() => {})
    // Give React time to consume hash tokens
    await page.waitForTimeout(3_000)
  }

  // Navigate to target path if not there
  if (!page.url().includes(path) && path !== '/') {
    await page.goto(`${LIVE_FRONTEND_URL}${path}`, { waitUntil: 'networkidle', timeout: 15_000 })
  }
}
