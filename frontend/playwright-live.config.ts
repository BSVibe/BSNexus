/**
 * Playwright config for live e2e tests against running services.
 * No webServer — assumes frontend (13100) and backend (18100) are already running.
 *
 * Usage:
 *   LIVE_FRONTEND_URL=http://localhost:13100 LIVE_API_URL=http://localhost:18100 \
 *   pnpm exec playwright test --config=playwright-live.config.ts e2e/specs/real-live.spec.ts
 */
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e/specs',
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  timeout: 120_000,
  use: {
    baseURL: process.env.LIVE_FRONTEND_URL || 'http://localhost:13100',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  // No webServer — services already running
})
