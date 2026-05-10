import { test, expect } from '@playwright/test'

import { injectAuth, blockSSORedirect } from '../helpers/mock-api'
import {
  installFounderMocks,
  makeDecision,
  makeDeliverable,
  makeFounderState,
  makeRequest,
  makeRun,
  type FounderMockState,
} from '../helpers/founder-mock'

/**
 * M0 quality suite — BENCHMARK_SPEC per file-disposition.md.
 *
 * Locks the UI surface across distinct state-machine paths the founder
 * might encounter in the first weeks of dogfooding. Real LLM quality
 * (10/10 product-ready, etc.) is the live M0 harness in
 * ``backend/src/quality/m0.py``; these specs lock the founder-side UI
 * so prompt iteration doesn't regress the surface.
 *
 * Greenfield rewrite (file-disposition.md §Frontend E2E Tests
 * "BENCHMARK_SPEC: keep scenario intent, rewrite runner/assertions"):
 *
 *   - All routes go through the flat A3 helper
 *     (``installFounderMocks``).
 *   - Inside-panel-only scenarios retired with the Inspector surface;
 *     ``blocked_error`` now asserts via the Brief ``blocked`` section
 *     (``BriefView`` reads ``running``/``blocked`` runs from
 *     ``GET /api/v1/runs``).
 *   - ``empty_content`` tightens to the ProjectPage empty state — the
 *     legacy chat-rail FAB / send path retired with GlobalChat.
 */

interface Scenario {
  name: string
  projectId: string
  setup: (state: FounderMockState) => void
  /** ProjectPage tab (``brief`` | ``decisions``). */
  tab: 'brief' | 'decisions'
  verify: (helpers: {
    expectVisible: (text: RegExp | string) => Promise<void>
    expectHidden: (text: RegExp | string) => Promise<void>
    state: FounderMockState
  }) => Promise<void>
}

const SCENARIOS: Scenario[] = [
  {
    name: 'happy_path',
    projectId: 'proj-m0-happy',
    tab: 'brief',
    setup: (s) => {
      s.requests.push(
        makeRequest({ id: 'req-h', project_id: s.projectId, intent_summary: 'Polish landing copy' }),
      )
      s.runs.push(
        makeRun({ id: 'run-h', project_id: s.projectId, request_id: 'req-h', status: 'done' }),
      )
      s.deliverables.push(
        makeDeliverable({
          id: 'del-h',
          project_id: s.projectId,
          request_id: 'req-h',
          title: 'landing-copy.md',
          type: 'doc',
          status: 'delivered',
          proof_state: 'verified',
          verifier_type: 'doc_lint',
          verification_exit_code: 0,
        }),
      )
    },
    verify: async ({ expectVisible }) => {
      await expectVisible('landing-copy.md')
    },
  },
  {
    name: 'blocked_decision',
    projectId: 'proj-m0-decision',
    tab: 'decisions',
    setup: (s) => {
      s.requests.push(
        makeRequest({ id: 'req-d', project_id: s.projectId, intent_summary: 'Add auth' }),
      )
      s.runs.push(
        makeRun({
          id: 'run-d',
          project_id: s.projectId,
          request_id: 'req-d',
          status: 'blocked',
          error_message: 'awaiting founder decision',
        }),
      )
      s.decisions.push(
        makeDecision({
          id: 'dec-d',
          project_id: s.projectId,
          request_id: 'req-d',
          origin_run_id: 'run-d',
          question: 'OAuth or magic links?',
          options: ['OAuth', 'Magic links'],
          blocking: true,
        }),
      )
    },
    verify: async ({ expectVisible }) => {
      await expectVisible('OAuth or magic links?')
    },
  },
  {
    name: 'blocked_error',
    projectId: 'proj-m0-error',
    tab: 'brief',
    setup: (s) => {
      // Greenfield Brief surfaces blocked Requests directly. The legacy
      // pattern of seeding a blocked ExecutionRun under an open Request
      // was a Run-centric artifact; in greenfield the Request itself
      // carries the status.
      s.requests.push(
        makeRequest({
          id: 'req-e',
          project_id: s.projectId,
          intent_summary: 'Tricky refactor',
          status: 'blocked',
        }),
      )
    },
    verify: async ({ expectVisible }) => {
      // Brief blocked section surfaces the request_intent of every
      // blocked run (BriefView ``RunRow`` renders ``r.request_intent``).
      await expectVisible(/Tricky refactor/)
    },
  },
  {
    name: 'empty_state',
    projectId: 'proj-m0-empty',
    tab: 'brief',
    setup: (_s) => {
      // No state seeded — Brief renders all five sections empty.
    },
    verify: async ({ state }) => {
      // No mutating call has been issued — sanity-check.
      expect(state.posts).toHaveLength(0)
      expect(state.requests).toHaveLength(0)
    },
  },
  {
    name: 'multiple_requests',
    projectId: 'proj-m0-multi',
    tab: 'brief',
    setup: (s) => {
      s.requests.push(
        makeRequest({ id: 'req-1', project_id: s.projectId, intent_summary: 'First task' }),
        makeRequest({ id: 'req-2', project_id: s.projectId, intent_summary: 'Second task' }),
      )
      s.runs.push(
        makeRun({ id: 'r-1', project_id: s.projectId, request_id: 'req-1', status: 'done' }),
        makeRun({ id: 'r-2', project_id: s.projectId, request_id: 'req-2', status: 'running' }),
      )
      s.deliverables.push(
        makeDeliverable({
          id: 'del-1',
          project_id: s.projectId,
          request_id: 'req-1',
          title: 'first-output.md',
          type: 'doc',
          status: 'delivered',
          proof_state: 'verified',
        }),
      )
    },
    verify: async ({ expectVisible }) => {
      await expectVisible('first-output.md')
    },
  },
]

test.describe('M0 quality suite — UI surface across state-machine paths', () => {
  for (const sc of SCENARIOS) {
    test(`scenario: ${sc.name}`, async ({ page }) => {
      await blockSSORedirect(page)
      await injectAuth(page)

      const state = makeFounderState(sc.projectId, sc.name)
      sc.setup(state)
      await installFounderMocks(page, state)

      const url = sc.tab === 'brief'
        ? `/projects/${sc.projectId}`
        : `/projects/${sc.projectId}?tab=${sc.tab}`
      await page.goto(url)

      const expectVisible = async (text: RegExp | string) => {
        await expect(page.getByText(text).first()).toBeVisible({ timeout: 10_000 })
      }
      const expectHidden = async (text: RegExp | string) => {
        await expect(page.getByText(text)).toHaveCount(0)
      }

      await sc.verify({ expectVisible, expectHidden, state })
    })
  }
})
