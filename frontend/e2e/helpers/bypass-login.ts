/**
 * E2E bypass login — injects a fake JWT into localStorage and intercepts
 * API requests with the E2E bypass token.
 *
 * Requires backend started with E2E_TEST_TOKEN=e2e-scenario-test-token
 */
import type { Page } from '@playwright/test'

const LIVE_FRONTEND_URL = process.env.LIVE_FRONTEND_URL || 'http://localhost:13100'
export const LIVE_API_URL = process.env.LIVE_API_URL || 'http://localhost:18100'

const BYPASS_TOKEN = 'e2e-scenario-test-token'

// Fake JWT that passes frontend's isExpired() and decodeJwt() checks.
// Header: {"alg":"none","typ":"JWT"}, Payload includes sub, email, app_metadata, exp=2100
const FAKE_JWT = [
  'eyJhbGciOiAibm9uZSIsICJ0eXAiOiAiSldUIn0',
  'eyJzdWIiOiAiZTJlLXRlc3QtdXNlciIsICJlbWFpbCI6ICJhZG1pbkBic3ZpYmUuZGV2IiwgImFwcF9tZXRhZGF0YSI6IHsidGVuYW50X2lkIjogImFiOGJmYjE1LWNiNjMtNDA2OC1hNjAwLTU0YjAyYjMzMzk2ZCIsICJyb2xlIjogImFkbWluIn0sICJ1c2VyX21ldGFkYXRhIjoge30sICJleHAiOiA0MTAyNDQ0ODAwLCAiaWF0IjogMTcwMDAwMDAwMCwgImlzcyI6ICJlMmUtdGVzdCIsICJhdWQiOiAiYXV0aGVudGljYXRlZCIsICJyb2xlIjogImF1dGhlbnRpY2F0ZWQifQ',
  '',
].join('.')

export async function bypassLoginAndNavigate(page: Page, path: string): Promise<void> {
  // Intercept all API calls and replace Authorization header with bypass token
  await page.route('**/api/**', async (route) => {
    const headers = {
      ...route.request().headers(),
      authorization: `Bearer ${BYPASS_TOKEN}`,
    }
    await route.continue({ headers })
  })

  // Navigate to the app to initialize localStorage domain
  await page.goto(`${LIVE_FRONTEND_URL}/`, { waitUntil: 'domcontentloaded', timeout: 10_000 })

  // Inject fake JWT into localStorage (frontend uses it for user state)
  await page.evaluate((token) => {
    localStorage.setItem('bsnexus_access_token', token)
  }, FAKE_JWT)

  // Navigate to target path — React picks up the token from localStorage
  await page.goto(`${LIVE_FRONTEND_URL}${path}`, { waitUntil: 'networkidle', timeout: 15_000 })
}
