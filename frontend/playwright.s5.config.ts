import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e/specs',
  fullyParallel: false,
  workers: 1,
  reporter: 'line',
  timeout: parseInt(process.env.PW_TIMEOUT || '600000', 10),
  use: {
    baseURL: process.env.LIVE_FRONTEND_URL || 'http://localhost:13100',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  // No webServer — using pre-started devcontainer frontend
})
