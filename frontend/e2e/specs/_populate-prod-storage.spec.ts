import { test as setup, expect } from '@playwright/test'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * One-shot helper that signs into nexus.bsvibe.dev with a known
 * BSVibe-Auth account and writes the resulting browser state to
 * ``e2e/.auth/prod-test-user.json`` for later reuse by
 * production-authed.spec.ts.
 *
 * Run via:
 *
 * ```sh
 * BSVIBE_TEST_EMAIL=user@bsvibe.dev \
 *   BSVIBE_TEST_PASSWORD="$(cat e2e/.auth/.user-pw)" \
 *   pnpm exec playwright test e2e/specs/populate-prod-storage.setup.ts \
 *     --project=chromium
 * ```
 */
const __dirname = dirname(fileURLToPath(import.meta.url))
const STORAGE_PATH = resolve(__dirname, '..', '.auth', 'prod-test-user.json')

setup('populate prod storage state via real BSVibe-Auth login', async ({
  page,
  context,
}) => {
  const EMAIL = process.env.BSVIBE_TEST_EMAIL
  const PASSWORD = process.env.BSVIBE_TEST_PASSWORD
  setup.skip(!EMAIL || !PASSWORD, 'Set BSVIBE_TEST_EMAIL/BSVIBE_TEST_PASSWORD')

  // Skip the BSNexus landing page; jump straight to the BSVibe-Auth
  // login URL with the canonical hash-route callback (matches what
  // useAuth.login() builds at runtime).
  const callback = encodeURIComponent('https://nexus.bsvibe.dev/#/auth/callback')
  await page.goto(`https://auth.bsvibe.dev/login?redirect_uri=${callback}`, {
    waitUntil: 'networkidle',
  })

  await page.fill('input[type="email"], input[name="email"]', EMAIL!)
  await page.fill('input[type="password"], input[name="password"]', PASSWORD!)

  await Promise.all([
    page.waitForURL(/^https:\/\/nexus\.bsvibe\.dev\//, { timeout: 30_000 }),
    page.click('button[type="submit"]'),
  ])

  // Wait for founder shell to render — confirms consumeAuthCallback()
  // ran and the token is in localStorage before we capture state.
  await expect(page.locator('textarea').first()).toBeVisible({
    timeout: 20_000,
  })
  await page.screenshot({
    path: resolve(__dirname, '..', '..', 'test-results', 'prod-authed', '00-after-login.png'),
    fullPage: true,
  })

  await context.storageState({ path: STORAGE_PATH })
  console.log('saved storage state to', STORAGE_PATH)
})
