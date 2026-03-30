import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'path'
import { homedir } from 'os'

const FRONTEND_BASE_URL = process.env.FRONTEND_BASE_URL || 'http://localhost:3000'

// Playwright browser system dependencies installed via micromamba
const CONDA_LIB = resolve(homedir(), '.mamba/envs/pw-deps/lib')
const CONDA_SHARE = resolve(homedir(), '.mamba/envs/pw-deps/share')
const FONTCONFIG_DIR = resolve(homedir(), '.config/fontconfig')

export default defineConfig({
  testDir: './e2e/specs',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [['html', { outputFolder: 'playwright-report' }], ['list']],
  use: {
    baseURL: FRONTEND_BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    launchOptions: {
      env: {
        ...process.env,
        LD_LIBRARY_PATH: [CONDA_LIB, process.env.LD_LIBRARY_PATH].filter(Boolean).join(':'),
        FONTCONFIG_PATH: FONTCONFIG_DIR,
        FONTCONFIG_FILE: resolve(FONTCONFIG_DIR, 'fonts.conf'),
        XDG_DATA_HOME: CONDA_SHARE,
      },
    },
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
})
