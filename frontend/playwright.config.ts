import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'path'
import { homedir } from 'os'

const FRONTEND_BASE_URL = process.env.FRONTEND_BASE_URL || 'http://localhost:3000'

// Playwright browser system dependencies installed via micromamba
const CONDA_LIB = resolve(homedir(), '.mamba/envs/pw-deps/lib')
const CONDA_SHARE = resolve(homedir(), '.mamba/envs/pw-deps/share')
const FONTCONFIG_DIR = resolve(homedir(), '.config/fontconfig')

// Live-LLM specs hit a real backend + Ollama and are intentionally
// excluded from the default run. Set ``LIVE_LLM=1`` (or use the
// ``test:e2e:live-llm`` script) to opt in. See
// ``e2e/specs/live-llm/README.md`` for the full setup checklist.
const LIVE_LLM_ENABLED = process.env.LIVE_LLM === '1'

export default defineConfig({
  testDir: './e2e/specs',
  testIgnore: LIVE_LLM_ENABLED ? undefined : ['**/live-llm/**'],
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
    // Phase B Batch 2: mobile viewport coverage. Pixel 5 (Mobile Chrome) +
    // iPhone 13 (Mobile Chromium engine — Playwright bundled WebKit hangs
    // page-launch on macOS 26 with DEPENDENCIES_VALIDATED stuck; the
    // viewport / userAgent / isMobile flag still match iPhone 13).
    // See Shared Library Roadmap §B3.
    {
      name: 'pixel-5',
      use: { ...devices['Pixel 5'] },
    },
    {
      name: 'iphone-13',
      use: {
        browserName: 'chromium',
        ...devices['iPhone 13'],
        defaultBrowserType: 'chromium',
      },
    },
  ],

  // ``webServer`` only auto-spawns ``pnpm dev`` on :3000 when the
  // caller has NOT pointed Playwright at an external server via
  // ``FRONTEND_BASE_URL``. When the env var is set (the standard
  // local + CI pattern — see ``test:e2e:isolated`` and the demo /
  // prod build flows in ``Docs/BSNexus/qa/e2e-2026-05-11/findings.md``),
  // any auto-spawned dev process would write to ``.next/`` and
  // clobber a parallel ``next start`` build serving on a different
  // port. Skip it.
  webServer: process.env.FRONTEND_BASE_URL
    ? undefined
    : {
        command: 'pnpm dev',
        url: 'http://localhost:3000',
        reuseExistingServer: !process.env.CI,
        timeout: 120000,
      },
})
