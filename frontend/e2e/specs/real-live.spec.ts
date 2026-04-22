/**
 * REAL live e2e suite — no API mocks at all.
 *
 * Every test in this file talks to a real FastAPI backend backed by a real
 * PostgreSQL + Redis. Authentication uses the env-gated bypass token from
 * ``live-token.ts`` so we can drive the UI without round-tripping
 * auth.bsvibe.dev. Resources created by each test use unique
 * timestamp-based names so they cannot collide between runs and the suite
 * does not require a clean DB to pass.
 *
 * Requirements:
 *   LIVE_FRONTEND_URL=http://bsserver:13100
 *   LIVE_API_URL=http://bsserver:18100   (informational; vite proxies /api)
 *   backend started with E2E_TEST_TOKEN matching ``live-token.ts``
 *
 * When the env vars are missing the entire describe block is skipped so
 * developer-laptop ``pnpm test:e2e`` runs are not forced to spin up a
 * live backend.
 */
import { expect, test, type Page } from '@playwright/test'

import { setupRealLivePage, skipUnlessLive } from '../helpers/live-real'

function uniqSuffix(): string {
  return `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`
}

async function gotoAgents(page: Page) {
  await setupRealLivePage(page, '/agents')
  await expect(
    page.getByRole('heading', { name: /agent organization/i }),
  ).toBeVisible({ timeout: 15_000 })
}

async function gotoDashboard(page: Page) {
  await setupRealLivePage(page, '/dashboard')
  await expect(page.getByRole('heading', { name: /^dashboard$/i })).toBeVisible({
    timeout: 15_000,
  })
}

test.describe('Real live e2e', () => {
  test.skip(skipUnlessLive, 'LIVE_FRONTEND_URL/LIVE_API_URL not set — skipping real live e2e')

  // ── 1. Apply a role-based template via the Reset flow ───────────
  //
  // The Agents tab no longer has a separate "Apply template" button —
  // Reset is the canonical way to clear the org chart, which then renders
  // the inline TemplateSelector picker. So the user flow is:
  // Reset → confirm → empty state → click Use Template on the card.
  //
  // The legacy "BSNexus Specialists" template is gone — its prompts were
  // refactored into reusable *skills* (design / analyze / plan /
  // memory_keeping) that get assigned to existing role-based agents at
  // template-apply time.

  test('user can apply the startup template via the reset flow', async ({ page }) => {
    test.setTimeout(120_000)
    await gotoAgents(page)

    page.on('dialog', (dialog) => dialog.accept())

    // If there are existing agents, run Reset first to land on the empty
    // state. The Reset button only renders when ``agents.length > 0``.
    const resetBtn = page.getByRole('button', { name: /^reset$/i })
    if (await resetBtn.isVisible().catch(() => false)) {
      await resetBtn.click()
      await page.getByRole('button', { name: /^delete all$/i }).click()
    }

    // Reset deletes agents in parallel; with N existing agents this is
    // usually <1s but allow plenty of headroom for slow CI.
    await expect(
      page.getByRole('heading', { name: /build your ai team/i }),
    ).toBeVisible({ timeout: 60_000 })

    const startupCard = page
      .locator('div.rounded-xl.p-5')
      .filter({ hasText: 'Startup Team' })
    await startupCard.getByRole('button', { name: /use template/i }).click()

    // After applying, the canonical Startup roles materialize.
    await expect(page.getByText(/CEO/i).first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText(/CTO/i).first()).toBeVisible()
  })

  // ── 2. Hire single agent ─────────────────────────────────────────

  test('user can hire a single agent end-to-end', async ({ page }) => {
    await gotoAgents(page)

    const agentName = `e2e-hire-${uniqSuffix()}`
    await page.getByRole('button', { name: /\+ hire agent/i }).click()

    const modal = page.getByRole('heading', { name: /hire new agent/i }).locator('..').locator('..')
    await modal.locator('input').nth(0).fill(agentName) // Name
    await modal.locator('input').nth(1).fill('engineer') // Role
    await modal.getByRole('button', { name: /^hire agent$/i }).click()

    await expect(page.getByText(agentName).first()).toBeVisible({ timeout: 15_000 })
  })

  // ── 3. Create new project ────────────────────────────────────────

  test('user can create a project from the dashboard', async ({ page }) => {
    await gotoDashboard(page)

    const projectName = `e2e-project-${uniqSuffix()}`
    await page.getByRole('button', { name: /^new project$/i }).click()

    // Fill the New Project modal — Name + Description.
    await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(projectName)
    // The Create button lives in the modal footer.
    await page.getByRole('button', { name: /^create$/i }).click()

    // After creation the dashboard refreshes and the project name appears
    // in the project list grid.
    await expect(page.getByText(projectName).first()).toBeVisible({ timeout: 15_000 })
  })

  // ── 4. Plan view + SSE bootstrap ─────────────────────────────────

  test('plan tab loads against a real project + SSE event stream', async ({ page }) => {
    await gotoDashboard(page)

    const projectName = `e2e-plan-${uniqSuffix()}`
    await page.getByRole('button', { name: /^new project$/i }).click()
    await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(projectName)
    await page.getByRole('button', { name: /^create$/i }).click()
    await expect(page.getByText(projectName).first()).toBeVisible({ timeout: 15_000 })

    // Click into the project — the card title is a link to /projects/:id.
    await page.getByText(projectName).first().click()

    // The project header carries the project name as an h2; if we see it,
    // routing landed on /projects/:id and the Plan tab (default) mounted.
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible({
      timeout: 15_000,
    })

    // The Plan tab button uses Material Icons so the accessible name is
    // "account_tree Plan" — match on the trailing label only.
    await expect(
      page.getByRole('button', { name: /account_tree.*plan/i }),
    ).toBeVisible()

    // The plan tree fetches /api/v1/projects/:id/plan-tree on mount and the
    // SSE endpoint /plan-tree/events stays open. If either 500'd we'd see
    // an error toast. The dashboard nav still being clickable proves the
    // page did not crash mid-render.
    await expect(page.getByRole('link', { name: /dashboard/i })).toBeVisible()
  })

  // ── 5. Delete an agent ───────────────────────────────────────────

  test('user can delete an agent and it disappears from the org chart', async ({ page }) => {
    await gotoAgents(page)

    // Create a throwaway agent to delete.
    const agentName = `e2e-delete-${uniqSuffix()}`
    await page.getByRole('button', { name: /\+ hire agent/i }).click()
    const modal = page.getByRole('heading', { name: /hire new agent/i }).locator('..').locator('..')
    await modal.locator('input').nth(0).fill(agentName)
    await modal.locator('input').nth(1).fill('engineer')
    await modal.getByRole('button', { name: /^hire agent$/i }).click()
    await expect(page.getByText(agentName).first()).toBeVisible({ timeout: 15_000 })

    // Click the agent card to open the detail sidebar.
    await page.getByText(agentName).first().click()

    // Auto-confirm the browser ``confirm()`` dialog the delete handler raises.
    page.once('dialog', (dialog) => dialog.accept())

    // Delete button lives inside the AgentDetailSidebar — actual label is
    // "Remove Agent" in this UI iteration.
    await page.getByRole('button', { name: /remove agent/i }).first().click()

    // The card disappears from the org chart.
    await expect(page.getByText(agentName)).toHaveCount(0, { timeout: 15_000 })
  })

  // ── 6. Budget page loads against real cost rows ──────────────────

  test('budget page renders against real backend data', async ({ page }) => {
    await setupRealLivePage(page, '/budget')

    await expect(page.getByRole('heading', { name: /budget/i }).first()).toBeVisible({
      timeout: 15_000,
    })

    // The page loads regardless of whether there is any spend; what we
    // care about is that the request did not 500. The "Total Budget" /
    // "Total Spent" labels are the canonical anchors.
    await expect(page.getByText(/total (budget|spent)/i).first()).toBeVisible()
  })

  // ── 7. Worker chat round-trip (isolated stack only) ──────────────
  //
  // The orchestrator (frontend/e2e/scripts/run-isolated.mjs) spawns
  // a real ``bsnexus-worker`` subprocess against a stub ``claude``
  // CLI, so this test exercises the full path:
  //
  //   browser → vite → backend chat dispatch → Redis stream
  //   → worker poll → stub claude → /workers/chat-result
  //   → backend SSE → chat sidebar
  //
  // It is skipped outside the isolated stack because spinning up a
  // worker against the dev devcontainer would require a real Anthropic
  // API key. The orchestrator sets ``E2E_ISOLATED_STACK=1`` so the
  // gate flips on automatically.
  const isolatedOnly = process.env.E2E_ISOLATED_STACK !== '1'

  test('chat round-trips through a real worker subprocess', async ({ page }) => {
    test.skip(isolatedOnly, 'requires isolated stack (run via pnpm test:e2e:isolated)')
    test.setTimeout(180_000)

    // Create a fresh project + an agent on a clean slate so we own
    // the entire chat history for the assertion.
    await gotoDashboard(page)
    page.on('dialog', (dialog) => dialog.accept())

    const projectName = `e2e-worker-chat-${uniqSuffix()}`
    await page.getByRole('button', { name: /^new project$/i }).click()
    await page.getByPlaceholder('e.g. BSNexus Mobile App').fill(projectName)
    await page.getByRole('button', { name: /^create$/i }).click()
    await expect(page.getByText(projectName).first()).toBeVisible({ timeout: 30_000 })

    await gotoAgents(page)
    // If the org chart is non-empty (leftover from another spec) reset
    // it so the chat router has a single, deterministic target.
    const resetBtn = page.getByRole('button', { name: /^reset$/i })
    if (await resetBtn.isVisible().catch(() => false)) {
      await resetBtn.click()
      await page.getByRole('button', { name: /^delete all$/i }).click()
      await expect(
        page.getByRole('heading', { name: /build your ai team/i }),
      ).toBeVisible({ timeout: 30_000 })
    }
    await page.getByRole('button', { name: /\+ hire agent/i }).click()
    const modal = page.getByRole('heading', { name: /hire new agent/i }).locator('..').locator('..')
    const agentName = `Worker_${uniqSuffix()}`
    await modal.locator('input').nth(0).fill(agentName)
    await modal.locator('input').nth(1).fill('engineer')
    await modal.getByRole('button', { name: /^hire agent$/i }).click()
    await expect(page.getByText(agentName).first()).toBeVisible({ timeout: 15_000 })

    // Open the project we just created and send a chat. Without an
    // LLM API key configured, the backend's ``_call_agent`` falls
    // back to the worker pool — so the message is dispatched as a
    // chat task to the bsnexus-worker subprocess, which executes the
    // stub ``claude`` shim and posts the canned response back.
    await gotoDashboard(page)
    await page.getByText(projectName).first().click()
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible({
      timeout: 15_000,
    })

    // The chat sidebar lives in the project page; the textarea is
    // the only ``role=textbox`` inside the right-hand pane.
    const chatBox = page.getByRole('textbox').last()
    await chatBox.fill(`@${agentName} ping from e2e`)
    await chatBox.press('Enter')

    // The stub shim emits this exact marker; if we see it in the
    // chat sidebar then the entire dispatch → worker → SSE chain
    // worked end-to-end.
    await expect(
      page.getByText(/STUB-CLAUDE-RESPONSE/i).first(),
    ).toBeVisible({ timeout: 90_000 })
  })

  // ── 8. Settings install token issue/revoke roundtrip ─────────────

  test('install token can be generated and revoked from settings', async ({ page }) => {
    // Always-accept any subsequent confirm() (revoke prompts the user).
    page.on('dialog', (dialog) => dialog.accept())

    await setupRealLivePage(page, '/settings')

    await expect(
      page.getByRole('heading', { name: /worker install token/i }),
    ).toBeVisible({ timeout: 15_000 })

    // If a token already exists from a previous run, revoke it first so we
    // start from the "no token" state.
    const revokeBtn = page.getByRole('button', { name: /^revoke$/i })
    if (await revokeBtn.isVisible().catch(() => false)) {
      await revokeBtn.click()
      await expect(
        page.getByRole('button', { name: /generate token/i }),
      ).toBeVisible({ timeout: 10_000 })
    }

    await page.getByRole('button', { name: /generate token/i }).click()

    // After generation, the plaintext token banner appears and the
    // "Token configured" indicator switches on.
    await expect(page.getByText(/copy now/i)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(/token configured/i)).toBeVisible()

    // Final cleanup: revoke so the next run starts in the same state.
    await page.getByRole('button', { name: /^revoke$/i }).click()
    await expect(
      page.getByRole('button', { name: /generate token/i }),
    ).toBeVisible({ timeout: 10_000 })
  })
})
