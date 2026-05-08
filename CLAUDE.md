# CLAUDE.md

Project instructions for Claude Code when working on BSNexus.

## Project Overview

BSNexus is the **shell for an AI company the user hires**. The user (as
founder) directs the company in a single chat; the backend decomposes
that direction into execution runs, composes prompts, optionally routes
audits through BSupervisor and knowledge through BSage, and surfaces the
results as deliverables plus any decisions that need founder approval.

Four user-facing surfaces per project:
- **Direction** — single Chief-of-Staff conversation. User messages are
  auto-classified (chit_chat / question / request / modification) and
  Request rows are created/appended accordingly.
- **Brief** — 5-section summary surface (shipped, needs decision,
  blocked, running, next). Decision-locks A2/O5: timeline became a
  subsection in PR4, the lead is the founder cards.
- **Decisions** — approval inbox, blocking items first.
- **Inside** (opt-in) — execution-run tree + composition-snapshot viewer
  for debugging.

There is no org chart, no per-agent chat, no @mentions, no Kanban board.
Those surfaces retired with the agent/task/phase tables in the
founder-metaphor migration.

## Core Stack

- **Python 3.11+** / **FastAPI** (async monolith)
- **PostgreSQL 16** + SQLAlchemy 2.0 (async) + Alembic
- **Redis Streams** (consumer groups, NOT Pub/Sub)
- **Two-path LLM dispatch** (Phase 2b, 2026-05-04 — BSVibe-optional
  philosophy revision of the 2026-05-03 reset):
  * `executor_type=bsgateway` → `core/bsgateway/client.py` →
    BSGateway worker pool (BSVibe infra path).
  * `executor_type=llm_api` → `core/llm/direct_client.py` →
    direct litellm call + MCP tool loop client-side (BSVibe-optional
    path; works without BSGateway).
  Both honor full MCP / Decisions / artifact UX. Capability is
  identical; the difference is *infra dependency*. Use `litellm` only
  inside `core/llm/`; everywhere else still goes through
  `BSGatewayClient`.
- **React 19** + TypeScript + Vite + Tailwind CSS + `@tanstack/react-query`
- **Package managers**: `uv` (Python), `pnpm` (Node.js)
- **Linting**: `ruff` (line-length 120)

## Project Structure

```
backend/src/
  api/                   # Route handlers (one router per resource)
    auth.py              # Supabase/BSVibe-auth bypass + token endpoints
    projects.py          # tenant-scoped CRUD
    conversation.py      # chat list + send (runs extractor)
    requests_api.py      # list requests per project
    deliverables.py      # list deliverables per project
    decisions.py         # list/resolve decisions
    inside.py            # read-only runs + composition snapshots
    integrations.py      # per-tenant BSage/BSGateway/BSupervisor config
  core/
    state_machine.py     # RunStateMachine (4-state ExecutionRun)
    run_orchestrator.py  # event-driven per-run dispatch (replaces GlobalDispatcher)
    bsgateway/
      client.py          # httpx wrapper over /api/v1/chat/completions
      adapter.py         # BSGatewayAdapter — orchestrator-facing executor
    composer/
      knowledge_client.py  # BSage thin wrapper / Noop fallback
      prompt_assembler.py  # pure composer over templates + fragments
    audit/
      audit_sink.py        # BSupervisor sync preflight + fire-and-forget post
    integrations/
      config.py            # TenantIntegrationSnapshot + 60s cache
    storage/
      deliverable_storage.py  # Protocol + dispatch
      git_storage.py          # wraps core/git_ops for code deliverables
      s3_storage.py           # MinIO (dev) / R2 (prod) via aioboto3
      local_storage.py        # filesystem dev fallback
    encryption.py        # EncryptionManager (API keys)
    tenant_context.py    # TenantMiddleware + get_tenant_id dep
    auth.py              # get_current_user dep + RBAC
  models/                # SQLAlchemy models, one file per domain
  schemas/               # Pydantic request/response schemas
  alembic/versions/      # DB migrations
  storage/               # Database + Redis clients
  queue/streams.py       # Redis Streams abstraction
  main.py                # App entry point + lifespan

frontend/src/
  api/                   # Axios clients (projects, conversation,
                         # integrations, founder)
  components/
    direction/           # DirectionView
    progress/            # ProgressView (timeline + trust cards)
    decisions/           # DecisionsView (inbox + resolve)
    inside/              # InsideView (runs tree + composition viewer)
    settings/            # IntegrationsTab + IntegrationCard
    common/              # Modal, Button, Badge, StatCard, Toast
    layout/              # Layout, Sidebar, Header
  hooks/useAuth.ts       # JWT resolver via BSVibe-Auth cross-subdomain SSO + hash-route OAuth callback
  pages/                 # Dashboard, Project, Settings, Landing
  types/founder.ts       # Snake-case mirrors of Pydantic schemas
  design-tokens.ts       # Source of truth; synced to ~/Docs/design_system.md
  index.css              # CSS vars derived from design_system.md

.devcontainer/           # postgres, redis, minio
```

## Development Commands

```bash
# Backend
docker compose -p bsnexus-founder \
  -f .devcontainer/docker-compose.yml \
  -f .devcontainer/docker-compose.override.yml \
  --env-file .devcontainer/.env \
  up -d postgres redis minio

DATABASE_URL="postgresql+asyncpg://bsnexus:bsnexus_dev@localhost:15434/bsnexus" \
REDIS_URL="redis://localhost:16381" \
E2E_TEST_TOKEN="dev-token" \
uv run --project backend alembic upgrade head
uv run --project backend uvicorn backend.src.main:app --host 0.0.0.0 --port 18100 --app-dir . --reload

# Tests
cd backend && uv run --project . pytest --no-header
# Fresh-PG smoke
cd backend && BSNEXUS_INTEGRATION_PG_URL="postgresql+asyncpg://bsnexus:bsnexus_dev@localhost:15434/bsnexus" \
  uv run --project . pytest tests/test_alembic_fresh_migration.py

# Frontend
cd frontend && pnpm install
VITE_API_URL=http://localhost:18100 \
VITE_AUTH_URL=https://auth.bsvibe.dev \
pnpm dev --host 0.0.0.0 --port 13100

cd frontend && pnpm exec tsc -b         # Type-check
cd frontend && pnpm tokens:verify       # Design-token drift guard
```

## MUST Rules

- **Async everywhere**: `async/await` for all I/O. No synchronous blocking.
- **Type hints**: On all functions and methods.
- **Pydantic models**: For all request/response schemas.
- **Redis Streams**: Consumer group pattern with JSON serialization and
  `XACK`. Never use Redis Pub/Sub.
- **Two-path LLM dispatch** (revised 2026-05-04, "BSVibe optional"):
  * `executor_type=bsgateway` → `core.bsgateway.BSGatewayClient`
    (HTTP `/api/v1/chat/completions`).
  * `executor_type=llm_api` → `core.llm.DirectLLMAdapter`
    (litellm + MCP tool loop client-side).
  `litellm` import is fenced into `core/llm/`; nowhere else may
  import it. Direct `openai` / `anthropic` SDK imports are still
  forbidden — litellm is the provider abstraction.
- **Tenant scoping**: Routes take `tenant_id: uuid.UUID = Depends(get_tenant_id)`
  and filter every query by it. Cross-tenant rows 404, never leak.
- **Dependency Injection**: Use FastAPI `Depends()` for DB sessions,
  Redis, tenant id, etc.
- **Decimal for money**: Use `Decimal`, never `float`. Cost amounts live
  in `cost_records.amount_cents` as `Integer`.
- **Env vars for secrets**: Validate with Pydantic BaseSettings.
  Document in `.env.example`.
- **Integration API keys are encrypted at rest**: via `EncryptionManager`.
  Never return raw keys — the API response only exposes `has_api_key`.
- **Tests required**: All new routers and state transitions ship with
  tests. Minimum 80% coverage is enforced by CI.
- **Degradable providers**: When BSage / BSGateway / BSupervisor is
  disabled for a tenant, the corresponding Noop provider is used and
  everything keeps working. Never raise out of provider boundaries.

## NEVER Rules

- Never use `sys.path.insert` or `sys.path.append`.
- Never use `requirements.txt` — use `pyproject.toml` + `uv`.
- Never hardcode secrets or API keys in code.
- Never use f-strings in raw SQL queries.
- Never include `Co-Authored-By` in commit messages.
- Never use `float` for monetary values.
- Never use synchronous blocking I/O.
- Never reintroduce an "Agent" / "Architect" / "Task" / "Phase" / "Goal"
  row — the founder-metaphor migration retired them deliberately. Use
  `Request`, `ExecutionRun`, `Deliverable`, `Decision`, and/or
  composition snapshots instead.
- Never bypass `RunStateMachine.transition()` for ExecutionRun status
  changes — it writes both history and milestone activity rows and
  publishes the run SSE event.
- Never expose another tenant's rows. Default to a 404 when the lookup
  finds no row in the active tenant's scope.

## API Endpoints

Flat REST resource shape — `/projects/{id}/<sub>` nesting was removed
2026-05-08 (decision-locks **A3**, see `~/Docs/BSNexus/planning/decision-locks.md`).
Sub-resources live at top-level URLs and accept `?project_id=` /
`?request_id=` query parameters; omitting the scope param returns the
tenant-wide cross-project view (powers the Home Decision Inbox strip,
the company brief, future Slack/email digests, etc.).

| Route                                          | Method        | Description |
|------------------------------------------------|---------------|-------------|
| `/health`, `/health/deps`                      | GET           | Liveness + PG/Redis connectivity |
| `/api/v1/projects`                             | GET/POST      | List + create projects |
| `/api/v1/projects/{id}`                        | GET/PATCH/DELETE | Project detail + update + delete |
| `/api/v1/messages?project_id={id}`             | GET           | Conversation list for a project |
| `/api/v1/messages`                             | POST          | Send message (body: `{content, project_id}`); runs the inline request rule |
| `/api/v1/requests?project_id={id}&limit=`      | GET           | Requests; omit `project_id` for cross-project tenant view |
| `/api/v1/deliverables?project_id={id}&limit=`  | GET           | Deliverables; omit `project_id` for cross-project tenant view |
| `/api/v1/deliverables/{id}/verify`             | POST          | Manually re-enqueue Verifier Worker for this deliverable (decision-locks A1) |
| `/api/v1/decisions?project_id={id}&blocking_only=&resolved=&limit=` | GET | Decision inbox; omit `project_id` for the Home Decision Inbox strip |
| `/api/v1/decisions/{id}/resolve`               | POST          | Resolve a decision |
| `/api/v1/brief?project_id={id}&limit=`         | GET           | 5-section Brief payload (decision-locks A2); omit `project_id` for the company Brief |
| `/api/v1/runs?request_id={id}`                 | GET           | Inside panel — runs for a request |
| `/api/v1/composition-snapshots/{id}`           | GET           | Inside panel — snapshot detail |
| `/api/v1/events?project_id={id}`               | GET           | SSE stream for a project (chat / runs / deliverables / decisions) |
| `/api/v1/workspace-files?project_id={id}`      | GET           | Workspace file tree |
| `/api/v1/workspace-files/content?project_id={id}&path={p}` | GET | Workspace file content |
| `/api/v1/integrations`                         | GET           | Redacted view of all three provider configs |
| `/api/v1/integrations/{provider}`              | PATCH         | Upsert one provider's config (encrypts api_key) |
| `/api/v1/integrations/{provider}/test`         | POST          | Probe reachable + auth |

All mutating endpoints require `Authorization: Bearer <jwt>`. Dev mode
accepts the raw value of `E2E_TEST_TOKEN` as a bypass.

There are no `/projects/{id}/<sub>` aliases — A3 locks "no backwards
compatibility shims". A cross-project listing is the same endpoint as the
project-scoped listing with the query param omitted; both share one
canonical URL pattern per resource.

## Request → Run Lifecycle

```
user message ─┐
              ├─► Conversation router persists message
              │
              └─► RequestExtractor
                    ├─ chit_chat / question → no side effect
                    ├─ request → new Request row, attach message.request_id
                    └─ modification → append to latest open Request

new Request ──► RunOrchestrator.dispatch_run(run)
                  1. Load TenantIntegrationSnapshot (60s cache)
                  2. resolve_knowledge_client(cfg)  ─► BSage REST or Noop
                  3. resolve_audit_sink(cfg)        ─► BSupervisor or Noop
                  4. PromptAssembler.compose(run, knowledge)
                  5. Persist CompositionSnapshot
                  6. audit.preflight (sync 200ms, fail-open)
                  7. state: pending → running
                  8. executor.execute(prompt, tools)
                  9. emit_post_async(audit, run, result)
                 10. state: running → done (or blocked)
                 11. enqueue_children whose deps are met
```

No polling loops. `RunOrchestrator` is event-driven. A single optional
`WorkerWatchdog` (1-min poll) reclaims orphaned remote-worker runs when
`REMOTE_WORKERS_ENABLED=true`.

## Run State Machine

```
pending → running → done
   ↑        ↓
   └──── blocked
```

- `pending`: not yet started or waiting on dependencies
- `running`: actively executing
- `blocked`: stuck — needs intervention or audit denial
- `done`: completed (terminal)

State changes go through `RunStateMachine.transition()`, which writes an
`ExecutionRunHistory` row + milestone `ExecutionRunActivity` row and
publishes a `run_transition` event to the per-project run event stream.

## Integrations (optional, per-tenant)

| Provider | Role | Connection |
|----------|------|------------|
| BSage | Graph-backed project memory. Composer pulls fragments. | `POST /api/knowledge/search` etc. |
| BSGateway | Cost-aware model selection. | LiteLLM `async_pre_call_hook` (no new endpoint). |
| BSupervisor | Pre-run safety audit. | `POST /api/events` (existing sync endpoint, sub-50ms). |

Each has a `TenantIntegrationConfig` row (encrypted api_key). When
disabled/unreachable, a Noop is used and BSNexus continues to work in
degraded mode; the Inside panel surfaces `composition.source = "local"`
and audit `degraded = true`.

## Tenant Model

- **Personal tenants**: A user without a `tenant_id` claim in the JWT
  gets a deterministic UUIDv5 personal tenant id derived from their
  user id. `ensure_personal_tenant` lazy-creates the row on first
  authenticated request.
- **Middleware**: `TenantMiddleware` stamps `request.state.tenant_id`.
  Routes consume it via `Depends(get_tenant_id)`.
- **Project scoping**: `Project.tenant_id` is a required FK
  (ON DELETE CASCADE). All per-project resources (Requests,
  ExecutionRuns, Deliverables, Decisions, CompositionSnapshots) are
  also directly tenant-scoped.

## Testing Patterns

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- **Test DB**: SQLite in-memory (via `aiosqlite`)
- **Redis mock**: `AsyncMock` for stream operations. Always set
  `tail.return_value = []` and yield with `asyncio.sleep(0)` in
  polling loops — busy AsyncMock loops hang otherwise.
- **Fixtures** (`tests/conftest.py`): `db_session`, `mock_stream_manager`,
  `mock_user`, `mock_tenant_id`, `seeded_tenant`, `client`.
- **Per-router contract tests**: one test file per router, asserting
  the 200/201/404/422/204 contracts, tenant isolation, and DB side
  effects.
- **Fresh-PG migration smoke**: `test_alembic_fresh_migration.py` —
  `alembic upgrade head` via subprocess against a throwaway PG.
  Skipped unless `BSNEXUS_INTEGRATION_PG_URL` is set.

## Design Tokens

- `frontend/src/design-tokens.ts` is the TS source of truth.
- CSS variables in `frontend/src/index.css` mirror it (manually synced;
  `pnpm tokens:verify` catches drift in CI).
- Values come from `~/Docs/design_system.md` v0.1.0 verbatim.
- BSNexus brand: `blue-500 #3b82f6`. Inside panel sibling badges:
  BSage=emerald, BSGateway=amber, BSupervisor=rose.

## Commit Style

Conventional Commits: `feat(scope):`, `fix(scope):`, `test(scope):`,
`docs(scope):`. No `Co-Authored-By` lines.
