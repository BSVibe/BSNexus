/**
 * BSNexus demo smoke tests.
 *
 * Test bodies live in `@bsvibe/demo/testing` so all four products run
 * identical assertions. Localhost defaults match
 * `_infra/scripts/demo-up-local.sh BSNexus`.
 *
 * Run locally:
 *   ~/Works/_infra/scripts/demo-up-local.sh BSNexus
 *   DEMO_E2E_BASE_URL=http://localhost:18600 \
 *   DEMO_E2E_API_URL=http://localhost:18600 \
 *     pnpm test:e2e --grep @demo
 */

import { runDemoSmokeSuite } from '@bsvibe/demo/testing'

runDemoSmokeSuite({
  product: 'BSNexus',
  baseUrl: process.env.DEMO_E2E_BASE_URL ?? 'http://localhost:18600',
  apiUrl: process.env.DEMO_E2E_API_URL ?? 'http://localhost:18600',
})
