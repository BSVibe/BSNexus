/**
 * Live-LLM end-to-end — Phase 1 functional smoke.
 *
 * Drives a real Direction → Request → ExecutionRun → Deliverable →
 * Verifier Worker → ``proof_state = verified`` cycle against a running
 * backend + a Tailscale-hosted Ollama. Excluded from the default e2e
 * run by ``playwright.config.ts`` (testIgnore on ``**\/live-llm/**``);
 * launch with ``pnpm test:e2e:live-llm``. See ``./README.md`` for setup.
 *
 * The "easy" scenario asks for a tiny add(a, b) + pytest pair. If the
 * worker can't get this far, the prompt + verifier loop has a real
 * regression and a stronger model won't save it.
 */

import { test, expect, request as playwrightRequest, type APIRequestContext } from '@playwright/test'

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:18100'
const E2E_TOKEN = process.env.E2E_TEST_TOKEN || 'dev-token'
const LIVE_LLM_MODEL = process.env.LIVE_LLM_MODEL || 'ollama_chat/qwen3-coder:30b'
const OLLAMA_BASE_URL = process.env.OLLAMA_BASE_URL || 'http://bsserver:11434'

// Locally-hosted Ollama models on bsserver routinely take 3–4 minutes
// for the easy / medium scenarios (qwen3-coder:30b walks 7–10 tool-call
// rounds before emitting the bsnexus-verification block). Cap the
// poller generously so a slow-but-correct run isn't reported as a
// product regression.
const DELIVERABLE_TIMEOUT_MS = 360_000
const PROOF_VERIFIED_TIMEOUT_MS = 60_000

interface Project {
  id: string
}

interface Deliverable {
  id: string
  proof_state: string
  verifier_type: string | null
  verification_exit_code: number | null
  proof_summary: string | null
  title: string
}

async function authedRequest(): Promise<APIRequestContext> {
  return playwrightRequest.newContext({
    baseURL: BACKEND_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${E2E_TOKEN}` },
  })
}

async function bootstrapExecutor(api: APIRequestContext): Promise<void> {
  // Idempotent — if an executor already exists pick the first one;
  // otherwise register a new Ollama-backed one keyed off env. The test
  // does not care about cleanup; the project workspace is scoped to
  // the project we create below and gets removed when we delete it.
  const list = await api.get('/api/v1/executor-configs')
  if (list.ok()) {
    const configs = (await list.json()) as Array<{ id: string }>
    if (configs.length > 0) return
  }
  const create = await api.post('/api/v1/executor-configs', {
    data: {
      name: 'live-llm-e2e',
      executor_type: 'llm_api',
      is_selected: true,
      config: {
        model: LIVE_LLM_MODEL,
        base_url: OLLAMA_BASE_URL,
        api_key: 'unused',
      },
    },
  })
  expect(create.ok(), `executor bootstrap failed: ${create.status()} ${await create.text()}`).toBe(
    true,
  )
}

async function createProject(api: APIRequestContext, name: string): Promise<Project> {
  const resp = await api.post('/api/v1/projects', { data: { name } })
  expect(resp.ok(), `project create failed: ${resp.status()} ${await resp.text()}`).toBe(true)
  return (await resp.json()) as Project
}

async function sendDirection(api: APIRequestContext, projectId: string, content: string): Promise<void> {
  const resp = await api.post('/api/v1/messages', {
    data: { project_id: projectId, content },
  })
  expect(resp.ok(), `direction send failed: ${resp.status()} ${await resp.text()}`).toBe(true)
}

async function pollDeliverable(
  api: APIRequestContext,
  projectId: string,
  opts: {
    timeoutMs: number
    predicate: (d: Deliverable) => boolean
    label: string
  },
): Promise<Deliverable> {
  const { timeoutMs, predicate, label } = opts
  const deadline = Date.now() + timeoutMs
  let last: Deliverable | null = null
  while (Date.now() < deadline) {
    const resp = await api.get(`/api/v1/deliverables?project_id=${projectId}&limit=10`)
    if (resp.ok()) {
      const items = (await resp.json()) as Deliverable[]
      for (const d of items) {
        last = d
        if (predicate(d)) return d
      }
    }
    await new Promise((r) => setTimeout(r, 2000))
  }
  throw new Error(
    `timed out waiting for ${label}; last deliverable: ${
      last ? JSON.stringify(last) : '(none seen)'
    }`,
  )
}

test.describe('live-llm — Direction → Verifier Worker → verified', () => {
  // ``workers: 1`` in playwright.config.ts already serialises across
  // the whole config; we don't need ``mode: 'serial'`` here. Removing
  // it makes each scenario independent — one failing test doesn't
  // skip the others, so a single dogfood run produces measurements
  // for all three scenarios even if smoke flakes (PR8 baseline data
  // collection rationale).
  test.describe.configure({ timeout: DELIVERABLE_TIMEOUT_MS + PROOF_VERIFIED_TIMEOUT_MS + 60_000 })

  test('smoke: python --version via shell_exec ends at proof_state=verified', async () => {
    // Minimum-trust live-LLM smoke: no file_write needed, just one
    // shell_exec + the bsnexus-verification block. The infrastructure
    // (parser → enqueue → SubprocessVerifier → state machine) is what
    // we're validating end-to-end here; ``add(a,b) + pytest``-grade
    // scenarios stress the LLM's tool-calling compliance and ride on
    // top of this passing first.
    const api = await authedRequest()
    try {
      await bootstrapExecutor(api)
      const project = await createProject(api, `live-smoke-${Date.now()}`)
      await sendDirection(
        api,
        project.id,
        [
          'Smoke check the verifier handoff. Skip the workspace context-read step,',
          'skip shell_exec, and end your chat reply with this EXACT text (copy it',
          'verbatim):',
          '\n\n```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"], "cwd": ".", "timeout_s": 10}\n```',
        ].join(' '),
      )

      // Wait for the run to materialise a deliverable that carries the
      // worker's verification block. The fenced-block parser is the
      // PR6 contract; if `verifier_type` is still null after this
      // wait, the worker stopped emitting the block.
      const stamped = await pollDeliverable(api, project.id, {
        timeoutMs: DELIVERABLE_TIMEOUT_MS,
        predicate: (d: Deliverable) => d.verifier_type !== null,
        label: 'deliverable with verifier_type stamped',
      })
      expect(stamped.verifier_type).toBe('software_test')

      // And then the verifier worker must transition the deliverable
      // to `verified` — that's the real proof, independent of the
      // worker's claimed exit code.
      const verified = await pollDeliverable(api, project.id, {
        timeoutMs: PROOF_VERIFIED_TIMEOUT_MS,
        predicate: (d: Deliverable) => d.id === stamped.id && d.proof_state === 'verified',
        label: 'deliverable proof_state=verified',
      })
      expect(verified.verification_exit_code).toBe(0)
    } finally {
      await api.dispose()
    }
  })

  test('easy: add(a,b) + pytest verifies clean', async () => {
    // Minimum-real-work scenario between smoke (no files) and medium
    // (FastAPI). Two file_writes + one shell_exec + the verification
    // block. Stresses multi-step tool-call discipline (the medium
    // scenario from PR7 baseline showed the LLM stopping after the
    // first artifact); easy is the smallest scenario that exercises
    // that behavior.
    const api = await authedRequest()
    try {
      await bootstrapExecutor(api)
      const project = await createProject(api, `live-easy-${Date.now()}`)
      await sendDirection(
        api,
        project.id,
        [
          'Create a tiny Python module with `add(a, b)` returning a + b in',
          '`add.py`, and a pytest at `tests/test_add.py` asserting',
          '`add(2, 3) == 5`. Use file_write for both files and run',
          '`python -m pytest tests/test_add.py -q` via shell_exec to verify.',
          'Pytest is already installed — no extra dependency install needed.',
        ].join(' '),
      )

      const stamped = await pollDeliverable(api, project.id, {
        timeoutMs: DELIVERABLE_TIMEOUT_MS,
        predicate: (d: Deliverable) => d.verifier_type !== null,
        label: 'easy-scenario deliverable with verifier_type stamped',
      })
      expect(stamped.verifier_type).toBe('software_test')

      const verified = await pollDeliverable(api, project.id, {
        timeoutMs: PROOF_VERIFIED_TIMEOUT_MS,
        predicate: (d: Deliverable) => d.id === stamped.id && d.proof_state === 'verified',
        label: 'easy-scenario deliverable proof_state=verified',
      })
      expect(verified.verification_exit_code).toBe(0)
    } finally {
      await api.dispose()
    }
  })

  test('medium: FastAPI /hello endpoint + TestClient smoke test verifies clean', async () => {
    // FastAPI, pytest, and httpx (TestClient's transport) are all
    // already in the BSNexus backend's Python env, so the worker
    // doesn't have to install anything — the verifier just runs
    // ``python -m pytest tests/`` from the project workspace.
    const api = await authedRequest()
    try {
      await bootstrapExecutor(api)
      const project = await createProject(api, `live-medium-${Date.now()}`)
      await sendDirection(
        api,
        project.id,
        [
          'Build a tiny FastAPI app at `app.py` with a `GET /hello` endpoint',
          'returning JSON `{"message": "hello"}`. Add a pytest at',
          '`tests/test_app.py` that uses `fastapi.testclient.TestClient` to',
          'GET /hello and assert status 200 and the JSON body matches.',
          'Use file_write for both files and run',
          '`python -m pytest tests/test_app.py -q` via shell_exec to verify.',
          'fastapi, pytest, and httpx are already installed — no extra',
          'dependency installs needed.',
        ].join(' '),
      )

      const stamped = await pollDeliverable(api, project.id, {
        timeoutMs: DELIVERABLE_TIMEOUT_MS,
        predicate: (d: Deliverable) => d.verifier_type !== null,
        label: 'medium-scenario deliverable with verifier_type stamped',
      })
      expect(stamped.verifier_type).toBe('software_test')

      const verified = await pollDeliverable(api, project.id, {
        timeoutMs: PROOF_VERIFIED_TIMEOUT_MS,
        predicate: (d: Deliverable) => d.id === stamped.id && d.proof_state === 'verified',
        label: 'medium-scenario deliverable proof_state=verified',
      })
      expect(verified.verification_exit_code).toBe(0)
    } finally {
      await api.dispose()
    }
  })
})
