#!/usr/bin/env node
//
// Isolated real-live e2e orchestrator.
//
// What this does, in order:
//
//   1. Spawns a throwaway PostgreSQL container on a random port.
//   2. Spawns a throwaway Redis container on a random port.
//   3. Runs ``alembic upgrade head`` against the fresh PG.
//   4. Spawns a uvicorn subprocess for the backend pointed at those
//      containers, with the e2e bypass token enabled.
//   5. Spawns a vite dev server proxying ``/api`` to the backend.
//   6. Spawns one ``bsnexus-worker`` subprocess, registers it against
//      the e2e tenant, and points it at a stub ``claude`` CLI so the
//      chat round-trip never touches Anthropic.
//   7. Runs the Playwright real-live spec against the fresh stack.
//   8. Tears EVERYTHING down — uvicorn / vite / worker processes,
//      both containers, the temp PATH dir for the stub.
//
// Run with: pnpm test:e2e:isolated  (from frontend/)
//
// Prereqs: docker, uv (for backend + alembic + worker), pnpm.
import { spawn, spawnSync } from 'node:child_process'
import { createServer } from 'node:net'
import { mkdtempSync, chmodSync, copyFileSync, openSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import process from 'node:process'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const FRONTEND_DIR = resolve(__dirname, '../..')
const REPO_ROOT = resolve(FRONTEND_DIR, '..')

const E2E_TOKEN =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlMmUtdGVzdC11c2VyIiwiZW1haWwiOiJlMmVAYnNuZXh1cy50ZXN0IiwiZXhwIjo5OTk5OTk5OTk5LCJhcHBfbWV0YWRhdGEiOnsidGVuYW50X2lkIjoiMTExMTExMTEtMTExMS00MTExLTgxMTEtMTExMTExMTExMTExIiwicm9sZSI6ImFkbWluIn19.e2e-fake-signature'
const E2E_TENANT = '11111111-1111-4111-8111-111111111111'

const stack = {
  pgContainer: '',
  redisContainer: '',
  pgPort: 0,
  redisPort: 0,
  apiPort: 0,
  fePort: 0,
  uvicornProc: null,
  viteProc: null,
  workerProc: null,
  workerCwd: '',
  workerStubDir: '',
}

function log(msg, ...rest) {
  console.log(`[isolated-e2e] ${msg}`, ...rest)
}

function run(cmd, args, opts = {}) {
  const result = spawnSync(cmd, args, { stdio: 'inherit', ...opts })
  if (result.status !== 0) {
    throw new Error(`${cmd} ${args.join(' ')} -> exit ${result.status}`)
  }
  return result
}

function runCapture(cmd, args, opts = {}) {
  const result = spawnSync(cmd, args, { encoding: 'utf8', ...opts })
  return { ok: result.status === 0, stdout: result.stdout || '', stderr: result.stderr || '' }
}

async function freePort() {
  return new Promise((resolveP, reject) => {
    const srv = createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port
      srv.close(() => resolveP(port))
    })
  })
}

async function waitForHttp(url, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url)
      if (res.status < 500) return
    } catch {
      /* not yet */
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`${label} did not become ready at ${url}`)
}

async function waitForPg(container, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const r = spawnSync('docker', ['exec', container, 'pg_isready', '-U', 'bsnexus', '-d', 'bsnexus'])
    if (r.status === 0) return
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`postgres ${container} did not become ready`)
}

async function authedFetch(path, init = {}) {
  const url = `http://127.0.0.1:${stack.apiPort}${path}`
  const res = await fetch(url, {
    ...init,
    headers: {
      Authorization: `Bearer ${E2E_TOKEN}`,
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  })
  return res
}

// ── Setup ───────────────────────────────────────────────────────────

async function spawnContainers() {
  const suffix = `e2e-${process.pid}-${Date.now().toString(36)}`
  stack.pgContainer = `bsnexus-${suffix}-pg`
  stack.redisContainer = `bsnexus-${suffix}-redis`
  stack.pgPort = await freePort()
  stack.redisPort = await freePort()

  log(`postgres -> ${stack.pgContainer} on 127.0.0.1:${stack.pgPort}`)
  run('docker', [
    'run',
    '-d',
    '--rm',
    '--name',
    stack.pgContainer,
    '-e',
    'POSTGRES_DB=bsnexus',
    '-e',
    'POSTGRES_USER=bsnexus',
    '-e',
    'POSTGRES_PASSWORD=bsnexus_dev',
    '-p',
    `127.0.0.1:${stack.pgPort}:5432`,
    'postgres:16-alpine',
  ])
  log(`redis -> ${stack.redisContainer} on 127.0.0.1:${stack.redisPort}`)
  run('docker', [
    'run',
    '-d',
    '--rm',
    '--name',
    stack.redisContainer,
    '-p',
    `127.0.0.1:${stack.redisPort}:6379`,
    'redis:7-alpine',
  ])

  await waitForPg(stack.pgContainer, 60_000)
}

function backendEnv() {
  return {
    ...process.env,
    DATABASE_URL: `postgresql+asyncpg://bsnexus:bsnexus_dev@127.0.0.1:${stack.pgPort}/bsnexus`,
    REDIS_URL: `redis://127.0.0.1:${stack.redisPort}`,
    // DEBUG=true is required to bypass the FATAL signing-key check
    // for the dev placeholder, but it also turns on sqlalchemy
    // echo=True which floods stdout. The orchestrator forwards every
    // child process line to its own stdout, so leave DEBUG on for
    // correctness and rely on grep when triaging.
    DEBUG: 'true',
    LOG_LEVEL: 'WARNING',
    PROMPT_SIGNING_KEY: 'dev-signing-key-not-for-production-0000',
    ENCRYPTION_KEY: 'dev-encryption-key-not-for-production-00',
    CORS_ALLOWED_ORIGINS: `["http://127.0.0.1:${stack.fePort}","http://localhost:${stack.fePort}"]`,
    RATE_LIMIT_ENABLED: 'false',
    WORKSPACE_BASE_DIR: join(stack.workerCwd, 'workspaces'),
    E2E_TEST_TOKEN: E2E_TOKEN,
    E2E_TEST_USER_TENANT_ID: E2E_TENANT,
    E2E_TEST_USER_ID: 'integration-test-user',
    E2E_TEST_USER_EMAIL: 'e2e@bsnexus.test',
  }
}

function migrate() {
  log('alembic upgrade head')
  run(
    'uv',
    ['run', '--project', 'backend', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
    { cwd: REPO_ROOT, env: backendEnv() },
  )
}

async function spawnBackend() {
  stack.apiPort = await freePort()
  // Tee uvicorn (very noisy with DEBUG=true) to a file rather than
  // stdout — sqlalchemy echo would otherwise drown vite + worker
  // output and the wait-for loops wouldn't see anything useful.
  const logPath = join(stack.workerCwd, 'uvicorn.log')
  log(`uvicorn -> 127.0.0.1:${stack.apiPort} (log: ${logPath})`)
  const fd = openSync(logPath, 'w')
  stack.uvicornProc = spawn(
    'uv',
    [
      'run',
      '--project',
      'backend',
      'uvicorn',
      'backend.src.main:app',
      '--host',
      '127.0.0.1',
      '--port',
      String(stack.apiPort),
    ],
    { cwd: REPO_ROOT, env: backendEnv(), stdio: ['ignore', fd, fd] },
  )
  await waitForHttp(`http://127.0.0.1:${stack.apiPort}/api/v1/agent-templates`, 60_000, 'uvicorn')
}

async function spawnVite() {
  stack.fePort = await freePort()
  log(`vite -> 127.0.0.1:${stack.fePort} (proxy /api -> ${stack.apiPort})`)
  // E2E_PROXY_TARGET is consumed by vite.config.ts to override the
  // dev proxy target. We deliberately do NOT set VITE_API_URL here —
  // that would leak the URL into the client bundle and bypass the
  // proxy entirely (axios would try a cross-origin call to the
  // ephemeral backend port, which CORS rejects).
  const env = { ...process.env, E2E_PROXY_TARGET: `http://127.0.0.1:${stack.apiPort}` }
  delete env.VITE_API_URL
  stack.viteProc = spawn(
    'pnpm',
    ['exec', 'vite', '--host', '127.0.0.1', '--port', String(stack.fePort)],
    { cwd: FRONTEND_DIR, env, stdio: ['ignore', 'pipe', 'pipe'] },
  )
  pipeChild(stack.viteProc, 'vite')
  // Vite serves SPA root → 200. Allow extra time for the first cold
  // dep optimization pass (~10-15s on a busy laptop).
  await waitForHttp(`http://127.0.0.1:${stack.fePort}/`, 90_000, 'vite')
}

function ensureNodeModulesForCurrentPlatform() {
  // The frontend's ``node_modules`` is sometimes hot-swapped by a
  // devcontainer running pnpm dev — that container ships linux
  // rollup binaries which crash when the orchestrator runs vite from
  // macOS. Run a no-op install before booting vite so the platform's
  // optional deps are present. ``pnpm install`` is a fast no-op when
  // the lockfile already matches.
  log('pnpm install (ensure platform deps)')
  run('pnpm', ['install'], { cwd: FRONTEND_DIR, env: { ...process.env, CI: 'true' } })
}

function makeStubPath() {
  // Drop a directory containing only the ``claude`` shim and prepend it
  // to PATH for the worker subprocess. The shim is checked into the repo
  // — copy it (rather than symlinking) so the temp dir survives any cwd
  // chdir games the worker plays.
  stack.workerStubDir = mkdtempSync(join(tmpdir(), 'bsnexus-stub-'))
  const dest = join(stack.workerStubDir, 'claude')
  const src = resolve(__dirname, 'stub-claude')
  copyFileSync(src, dest)
  chmodSync(dest, 0o755)
  return stack.workerStubDir
}

async function mintInstallToken() {
  // The e2e tenant is not the all-zero default, so open-mode worker
  // registration is rejected with 401. Mint a real install token via
  // the admin endpoint and pass it on the register call.
  const res = await authedFetch('/api/v1/settings/install-token', {
    method: 'POST',
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`mint install token failed: ${res.status} ${text}`)
  }
  const data = await res.json()
  if (!data.token) {
    throw new Error(`mint install token: no token in response: ${JSON.stringify(data)}`)
  }
  return data.token
}

async function registerWorker(installToken) {
  const res = await authedFetch('/api/v1/workers/register', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Install-Token': installToken,
    },
    body: JSON.stringify({
      name: `e2e-worker-${process.pid}`,
      capabilities: ['claude_code'],
    }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`worker register failed: ${res.status} ${text}`)
  }
  const data = await res.json()
  return { workerToken: data.token, workerName: data.name || 'e2e-worker' }
}

async function spawnWorker() {
  const stubDir = makeStubPath()
  const cwd = mkdtempSync(join(tmpdir(), 'bsnexus-worker-'))
  stack.workerCwd = cwd

  log(`minting install token + registering worker against ${stack.apiPort}`)
  const installToken = await mintInstallToken()
  const { workerToken, workerName } = await registerWorker(installToken)

  log(`worker -> stub claude at ${stubDir}`)
  stack.workerProc = spawn(
    'uv',
    ['run', '--project', resolve(REPO_ROOT, 'worker'), 'bsnexus-worker', 'run'],
    {
      cwd,
      env: {
        // Important: build PATH from a tiny allowlist (stub dir + system
        // bins) so the worker's auto-detect picks up the stub instead of
        // any real ``claude`` that might be installed on the host.
        PATH: `${stubDir}:/usr/local/bin:/usr/bin:/bin:${process.env.PATH || ''}`,
        BSNEXUS_SERVER_URL: `http://127.0.0.1:${stack.apiPort}`,
        BSNEXUS_WORKER_TOKEN: workerToken,
        BSNEXUS_WORKER_NAME: workerName,
        BSNEXUS_POLL_INTERVAL_SECONDS: '1',
        HOME: process.env.HOME || '',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  pipeChild(stack.workerProc, 'worker')

  // Wait until backend reports a worker as online so chat dispatch
  // does not race the first poll.
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    const res = await authedFetch('/api/v1/workers')
    if (res.ok) {
      const workers = await res.json()
      if (workers.some((w) => w.status === 'online')) return
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error('worker did not register as online within 30s')
}

function pipeChild(child, label) {
  child.stdout.on('data', (chunk) => process.stdout.write(`[${label}] ${chunk}`))
  child.stderr.on('data', (chunk) => process.stderr.write(`[${label}] ${chunk}`))
  child.on('exit', (code) => {
    if (code !== 0 && code !== null) {
      log(`${label} exited with code ${code}`)
    }
  })
}

// ── Run + teardown ──────────────────────────────────────────────────

async function runTests() {
  log('running playwright real-live + worker-chat specs')
  return new Promise((resolveP) => {
    const proc = spawn(
      'pnpm',
      [
        'exec',
        'playwright',
        'test',
        'real-live',
        '--reporter=line',
        '--workers=1',
      ],
      {
        cwd: FRONTEND_DIR,
        env: {
          ...process.env,
          LIVE_FRONTEND_URL: `http://127.0.0.1:${stack.fePort}`,
          LIVE_API_URL: `http://127.0.0.1:${stack.apiPort}`,
          // Gate the worker-chat scenario on the isolated stack flag —
          // it requires a real bsnexus-worker subprocess and stub
          // claude, both of which only exist when this script booted
          // the stack itself.
          E2E_ISOLATED_STACK: '1',
        },
        stdio: 'inherit',
      },
    )
    proc.on('exit', (code) => resolveP(code ?? 1))
  })
}

function killProc(child, label) {
  if (!child) return
  try {
    child.kill('SIGTERM')
  } catch {
    /* already gone */
  }
  log(`killed ${label}`)
}

function rmContainer(name) {
  if (!name) return
  spawnSync('docker', ['rm', '-f', name], { stdio: 'ignore' })
  log(`removed container ${name}`)
}

function teardown() {
  log('tearing down...')
  killProc(stack.workerProc, 'worker')
  killProc(stack.viteProc, 'vite')
  killProc(stack.uvicornProc, 'uvicorn')
  rmContainer(stack.redisContainer)
  rmContainer(stack.pgContainer)
  // Stub dir + worker cwd are tmp — let the OS reap them.
}

let teardownDone = false
function safeTeardown() {
  if (teardownDone) return
  teardownDone = true
  try {
    teardown()
  } catch (err) {
    log('teardown error', err)
  }
}

process.on('SIGINT', () => {
  safeTeardown()
  process.exit(130)
})
process.on('SIGTERM', () => {
  safeTeardown()
  process.exit(143)
})

async function main() {
  // Pre-flight: docker available?
  const docker = runCapture('docker', ['info'])
  if (!docker.ok) {
    log('docker is not available — aborting')
    process.exit(2)
  }

  let exitCode = 1
  try {
    // workerCwd is referenced by backendEnv (WORKSPACE_BASE_DIR), so
    // make a temp dir up front before we build any env objects.
    stack.workerCwd = mkdtempSync(join(tmpdir(), 'bsnexus-ws-'))

    ensureNodeModulesForCurrentPlatform()
    await spawnContainers()
    migrate()
    await spawnBackend()
    await spawnVite()
    await spawnWorker()

    exitCode = await runTests()
  } catch (err) {
    log('FATAL', err)
    exitCode = 1
  } finally {
    safeTeardown()
  }
  process.exit(exitCode)
}

main()
