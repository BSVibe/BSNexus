/**
 * API integration tests are skipped in E2E suite.
 * These tests require a running backend and are handled separately.
 * All UI E2E tests use page.route() mocks instead.
 */
import { test } from '@playwright/test'

test.skip('API integration tests require a running backend', () => {
  // Placeholder — API integration tests moved to backend test suite
})
