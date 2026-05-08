import { test, expect, type Page } from '@playwright/test'

import { injectAuth, blockSSORedirect } from '../helpers/mock-api'
import {
  installFounderMocks,
  makeFounderState,
  makeRequest,
  makeRun,
  type FounderMockState,
  type MockRunActivity,
  type MockRunSummary,
} from '../helpers/founder-mock'

/**
 * PR7 — failure-mode instrumentation Inside-panel surface.
 *
 * Validates:
 * - <RunQualityCard /> renders the dominant_reply_quality label, rounds,
 *   tool calls, files, fenced-block badge, failure-signal warning.
 * - <RunActivityTimeline /> shows milestone rows by default and
 *   reveals tool rows when "Show tool log" is toggled.
 * - <FailureModeStrip /> renders the project's last-N aggregate.
 * - Mobile viewport (375x812) renders without overflow.
 *
 * Pure mock-API spec — no live LLM dependency. Uses the founder-mock
 * helper (extended in this PR for run-summaries / run-activities A3
 * routes). Auth, SSE, and the supporting routes follow the
 * golden-path pattern.
 */

const PROJECT_ID = 'proj-pr7'
const RUN_ID = 'run-pr7-1'

function clean_summary(): MockRunSummary {
  return {
    total_rounds: 3,
    total_tool_calls: 2,
    per_round: [
      {
        round_idx: 0,
        content_chars: 20,
        tool_call_count: 1,
        reply_quality: 'real_tool_calls',
        finish_reason: 'tool_calls',
      },
      {
        round_idx: 1,
        content_chars: 20,
        tool_call_count: 1,
        reply_quality: 'real_tool_calls',
        finish_reason: 'tool_calls',
      },
      {
        round_idx: 2,
        content_chars: 120,
        tool_call_count: 0,
        reply_quality: 'real_tool_calls',
        finish_reason: 'stop',
      },
    ],
    dominant_reply_quality: 'real_tool_calls',
    did_emit_fenced_block: true,
    files_actually_written: ['add.py', 'tests/test_add.py'],
    failure_signals: [],
  }
}

function activities_for(runId: string, projectId: string): MockRunActivity[] {
  // One milestone + one tool-call pair, kept tiny so assertions are
  // unambiguous.
  return [
    {
      id: `${runId}-act-1`,
      run_id: runId,
      project_id: projectId,
      level: 'milestone',
      event_type: 'llm_round_complete',
      summary: '[round 0] 20 chars, 1 tool calls',
      detail: { round_idx: 0 },
      created_at: '2026-05-08T15:00:00Z',
    },
    {
      id: `${runId}-act-2`,
      run_id: runId,
      project_id: projectId,
      level: 'tool',
      event_type: 'tool_call_start',
      summary: '[round 0] file_write(…) start',
      detail: { round_idx: 0, tool_name: 'file_write' },
      created_at: '2026-05-08T15:00:01Z',
    },
    {
      id: `${runId}-act-3`,
      run_id: runId,
      project_id: projectId,
      level: 'tool',
      event_type: 'tool_call_done',
      summary: '[round 0] file_write → ok 120ms',
      detail: { round_idx: 0, outcome: 'ok' },
      created_at: '2026-05-08T15:00:02Z',
    },
  ]
}

async function setup(page: Page, summary: MockRunSummary | null): Promise<FounderMockState> {
  await blockSSORedirect(page)
  await injectAuth(page)
  const state = makeFounderState(PROJECT_ID, 'PR7 instrumentation')
  state.requests.push(
    makeRequest({
      id: 'req-pr7',
      project_id: PROJECT_ID,
      intent_summary: 'Build add(a,b) + pytest',
    }),
  )
  state.runs.push(
    makeRun({
      id: RUN_ID,
      project_id: PROJECT_ID,
      request_id: 'req-pr7',
      status: 'done',
      composition_snapshot_id: 'snap-001',
      run_summary: summary,
    }),
  )
  state.activities.push(...activities_for(RUN_ID, PROJECT_ID))
  await installFounderMocks(page, state)
  return state
}

test.describe('PR7 — Inside panel failure-mode instrumentation', () => {
  test('clean run shows real_tool_calls quality + activity timeline + failure-mode strip', async ({
    page,
  }) => {
    await setup(page, clean_summary())
    await page.goto(`/projects/${PROJECT_ID}?tab=inside&focusRequest=req-pr7`)

    // FailureModeStrip — top-of-tab pill row with last-N counts.
    const strip = page.getByTestId('failure-mode-strip')
    await expect(strip).toBeVisible({ timeout: 5000 })
    await expect(strip.getByTestId('fms-real_tool_calls')).toBeVisible()

    // RunQualityCard — dominant kind = real_tool_calls (emerald).
    const card = page.getByTestId('run-quality-card')
    await expect(card).toBeVisible()
    await expect(card).toHaveAttribute('data-quality', 'real_tool_calls')
    // Rounds + tool calls + files counts surfaced as labelled pills.
    await expect(card).toContainText(/3/)
    await expect(card).toContainText(/2/)

    // RunActivityTimeline — milestone visible, tool rows hidden until toggled.
    const timeline = page.getByTestId('run-activity-timeline')
    await expect(timeline).toBeVisible()
    await expect(timeline).toContainText('20 chars, 1 tool calls')
    // Tool log button reveals the tool rows.
    const toggle = page.getByTestId('toggle-tool-log')
    await expect(toggle).toBeVisible()
    await toggle.click()
    await expect(timeline).toContainText('file_write → ok 120ms')
  })

  test('null summary shows "no quality summary yet" placeholder', async ({ page }) => {
    await setup(page, null)
    await page.goto(`/projects/${PROJECT_ID}?tab=inside&focusRequest=req-pr7`)
    const card = page.getByTestId('run-quality-card')
    // Element exists but renders the empty placeholder; the
    // data-quality attribute is absent on the empty variant.
    // (We assert visibility of the *container* via class — the empty
    //  placeholder doesn't carry the data-testid.)
    await expect(page.locator('.run-quality-card--empty')).toBeVisible()
    await expect(card).toHaveCount(0)
  })

  test('failure-signal warning surfaces when LLM emitted fenced block but no files', async ({
    page,
  }) => {
    const summary: MockRunSummary = {
      ...clean_summary(),
      total_tool_calls: 0,
      files_actually_written: [],
      did_emit_fenced_block: true,
      dominant_reply_quality: 'fenced_block_only',
      failure_signals: [
        'Emitted bsnexus-verification fenced block but never invoked file_write — verifier will fail.',
      ],
    }
    await setup(page, summary)
    await page.goto(`/projects/${PROJECT_ID}?tab=inside&focusRequest=req-pr7`)
    const card = page.getByTestId('run-quality-card')
    await expect(card).toHaveAttribute('data-quality', 'fenced_block_only')
    await expect(card).toContainText('verifier will fail')
  })

  test('mobile viewport (375x812) renders without horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await setup(page, clean_summary())
    await page.goto(`/projects/${PROJECT_ID}?tab=inside&focusRequest=req-pr7`)
    await expect(page.getByTestId('failure-mode-strip')).toBeVisible({ timeout: 5000 })
    await expect(page.getByTestId('run-quality-card')).toBeVisible()
    // No element wider than the viewport (catches accidental fixed
    // widths on the new components).
    const widest = await page.evaluate(() => {
      let max = 0
      for (const el of Array.from(document.querySelectorAll('body *'))) {
        const w = (el as HTMLElement).getBoundingClientRect().width
        if (w > max) max = w
      }
      return max
    })
    expect(widest).toBeLessThanOrEqual(375 + 1)
  })
})
