# BSNexus

The shell for an AI company you hire. You direct the company in a
single chat; BSNexus decomposes the direction into execution runs,
composes prompts, optionally routes audits through BSupervisor and
knowledge through BSage, and surfaces the results as deliverables
plus any decisions that need your approval.

There is no org chart, no per-agent chat, no @mentions, no Kanban
board. The metaphor is **founder hiring an AI company** — direction
in, decisions approved, deliverables out.

## Four user-facing surfaces (per project)

- **Direction** — single Chief-of-Staff conversation. User messages
  are auto-classified (chit_chat / question / request / modification)
  and Request rows are created/appended accordingly.
- **Progress** — deliverable timeline + three trust cards showing
  whether BSage / BSGateway / BSupervisor are connected.
- **Decisions** — approval inbox, blocking items first.
- **Inside** (opt-in) — execution-run tree + composition-snapshot
  viewer for debugging.

## Architecture

```
                ┌──────────────┐
                │   Frontend   │  React 19 + Vite
                │ (port 13100) │  TypeScript + Tailwind
                └──────┬───────┘
                       │ HTTP / SSE
                ┌──────┴───────┐
                │   Backend    │  FastAPI (async)
                │ (port 18100) │  SQLAlchemy 2.0 (async)
                └──┬───────┬───┘
                   │       │
         ┌─────────┘       └─────────┐
         ▼                           ▼
  ┌────────────┐              ┌────────────┐
  │ PostgreSQL │              │   Redis    │
  │  (storage) │              │ (streams)  │
  └────────────┘              └────────────┘

Per-tenant optional integrations (all degradable, Noop fallback):
  BSage         — knowledge graph (search, retrieve, link)
  BSGateway     — model routing via LiteLLM async_pre_call_hook
  BSupervisor   — pre-run safety audit (sync, sub-50ms, fail-open)
```

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ with `uv`
- Node.js 20+ with `pnpm`

### Setup

```bash
# 1. Infrastructure (postgres, redis, minio)
docker compose -p bsnexus-founder \
  -f .devcontainer/docker-compose.yml \
  -f .devcontainer/docker-compose.override.yml \
  --env-file .devcontainer/.env \
  up -d postgres redis minio

# 2. Backend migrations + server (port 18100)
cd backend
DATABASE_URL="postgresql+asyncpg://bsnexus:bsnexus_dev@localhost:15434/bsnexus" \
REDIS_URL="redis://localhost:16381" \
E2E_TEST_TOKEN="dev-token" \
uv run --project . alembic upgrade head

DATABASE_URL="postgresql+asyncpg://bsnexus:bsnexus_dev@localhost:15434/bsnexus" \
REDIS_URL="redis://localhost:16381" \
E2E_TEST_TOKEN="dev-token" \
uv run --project . uvicorn backend.src.main:app --host 0.0.0.0 --port 18100 --app-dir .. --reload

# 3. Frontend (port 13100)
cd frontend
pnpm install
VITE_API_URL=http://localhost:18100 \
VITE_AUTH_URL=https://auth.bsvibe.dev \
pnpm dev --host 0.0.0.0 --port 13100
```

Open http://localhost:13100 — the frontend authenticates via
BSVibe-Auth (`auth.bsvibe.dev`) cross-subdomain cookie SSO, or by
landing on `#/auth/callback` with tokens in the URL fragment. There
is no client-side dev-bypass token — local Playwright runs send the
backend's `E2E_TEST_TOKEN` directly as the `Authorization` header.

## Tech Stack

| Layer        | Technology                                                          |
| ------------ | ------------------------------------------------------------------- |
| Frontend     | React 19, TypeScript, Vite, Tailwind CSS, TanStack Query, SSE       |
| Backend      | Python 3.11+, FastAPI, SQLAlchemy 2.0 async, Alembic, structlog     |
| Queue        | Redis Streams (consumer groups, JSON, XACK)                         |
| Database     | PostgreSQL 16                                                       |
| LLM          | LiteLLM (provider-agnostic) with hard `asyncio.wait_for` backstops  |
| Storage      | MinIO (dev) / R2 (prod) for non-code deliverables; git for code     |
| Auth         | BSVibe SSO (JWT, JWKS) — dev bypass via `E2E_TEST_TOKEN`            |
| Package Mgmt | uv (Python), pnpm (Node.js)                                         |

## Request → Run Lifecycle

```
user message → conversation router persists message
            ↓
            RequestExtractor (LiteLLM classifier)
              ├─ chit_chat / question → no side effect
              ├─ request → new Request row
              └─ modification → append to latest open Request

new Request → RunOrchestrator.dispatch_run(run)
                1. Load TenantIntegrationSnapshot (60s cache)
                2. resolve_knowledge_client(cfg) → BSage REST or Noop
                3. resolve_audit_sink(cfg)        → BSupervisor or Noop
                4. PromptAssembler.compose(run, knowledge)
                5. Persist CompositionSnapshot
                6. audit.preflight (sync 200ms, fail-open)
                7. state: pending → running
                8. executor.execute(prompt, tools)
                9. emit_post_async(audit, run, result)
               10. state: running → done (or blocked)
               11. enqueue_children whose deps are met
```

The replanner is iterative (agile, not waterfall): after each phase
completes it picks the SINGLE next step or declares `done`. Hard
rules include intent decomposition for multi-deliverable founder
intents and no-duplicate-work checking against `workspace_files`.

## API Documentation

FastAPI auto-generated docs at http://localhost:18100/docs.

Selected routes:

| Route                                  | Method      | Description                          |
|----------------------------------------|-------------|--------------------------------------|
| `/api/v1/projects`                     | GET / POST  | List + create projects               |
| `/api/v1/projects/{id}/messages`       | GET / POST  | Conversation list + send             |
| `/api/v1/projects/{id}/events`         | GET (SSE)   | Real-time per-project event stream   |
| `/api/v1/projects/{id}/deliverables`   | GET         | Deliverables for a project           |
| `/api/v1/projects/{id}/decisions`      | GET         | Decisions inbox (blocking first)     |
| `/api/v1/decisions/{id}/resolve`       | POST        | Resolve a decision                   |
| `/api/v1/integrations`                 | GET         | Redacted view of provider configs    |
| `/api/v1/integrations/{provider}/test` | POST        | Probe reachability + auth            |

## Development

### Tests

```bash
# Backend (pytest, 261 tests, 75%+ coverage)
cd backend && uv run --project . pytest --no-header

# Fresh-PG migration smoke
cd backend && BSNEXUS_INTEGRATION_PG_URL="postgresql+asyncpg://bsnexus:bsnexus_dev@localhost:15434/bsnexus" \
  uv run --project . pytest tests/test_alembic_fresh_migration.py
```

### Lint / Type-check

```bash
# Backend
cd backend && uv run --project . ruff check src/

# Frontend
cd frontend && pnpm lint
cd frontend && pnpm exec tsc -b

# Design-token drift guard
cd frontend && pnpm tokens:verify
```

### Hung-run debugging (macOS, no py-spy)

The backend installs SIGUSR1 / SIGUSR2 handlers on startup. If a
run appears stuck:

```bash
# find the worker pid (uvicorn --reload spawns a multiprocessing child)
WORKER=$(pgrep -P "$(pgrep -f 'uvicorn.*backend.src.main' | head -1)" \
         | xargs -I{} sh -c 'ps -p {} -o pid=,etime= 2>/dev/null' \
         | sort -k2 | tail -1 | awk '{print $1}')

kill -USR2 "$WORKER"   # asyncio task stacks
kill -USR1 "$WORKER"   # OS thread tracebacks
tail -f /tmp/bsnexus-trace.log
```

## Project Structure

```
backend/
├── src/
│   ├── api/                # Route handlers (one router per resource)
│   ├── core/
│   │   ├── orchestrator_adapter.py   # LiteLLM tool-calling loop
│   │   ├── run_orchestrator.py       # event-driven per-run dispatch
│   │   ├── dispatcher.py             # 3-phase session management
│   │   ├── planner.py                # iterative replanner
│   │   ├── composer/                 # prompt assembly + knowledge client
│   │   ├── audit/                    # BSupervisor sink + Noop fallback
│   │   ├── prompts/                  # YAML-backed prompt registry (A/B variants)
│   │   ├── integrations/             # per-tenant config + 60s cache
│   │   ├── storage/                  # deliverable storage abstraction
│   │   └── project_workspace.py      # workspace listing (noise-filtered)
│   ├── models/                       # SQLAlchemy models
│   ├── schemas/                      # Pydantic request/response schemas
│   ├── alembic/versions/             # DB migrations
│   ├── queue/                        # Redis Streams abstraction
│   └── main.py                       # App entry + SIGUSR1/2 tracer
└── tests/                            # pytest, asyncio mode

frontend/
├── src/
│   ├── api/                          # Axios clients
│   ├── components/
│   │   ├── direction/                # Chat (Direction surface)
│   │   ├── progress/                 # Timeline + trust cards
│   │   ├── decisions/                # Approval inbox
│   │   ├── inside/                   # Run tree + snapshot viewer
│   │   ├── settings/                 # IntegrationsTab
│   │   ├── common/                   # Modal, Button, Badge, …
│   │   └── layout/                   # Sidebar, Header
│   ├── hooks/
│   │   ├── useAuth.ts                # JWT resolver
│   │   └── useProjectEvents.ts       # SSE subscription
│   ├── pages/                        # Dashboard, Project, Settings
│   ├── design-tokens.ts              # TS source of truth → CSS vars
│   └── index.css                     # CSS vars (synced via pnpm tokens:verify)
└── public/

worker/                                # Optional remote worker
└── (only used when REMOTE_WORKERS_ENABLED=true)

docs/
├── known-issues/                     # Active long-term watches
└── product-direction/                 # Trust, git integration, OSS mode
```

## Documentation

- [docs/architecture.md](./docs/architecture.md) — class / sequence / ER / state diagrams (Mermaid)
- [CLAUDE.md](./CLAUDE.md) — project rules + architecture for AI assistants
- [docs/known-issues/](./docs/known-issues/) — open issues under long-term watch
  - [llm-tool-loop-hang.md](./docs/known-issues/llm-tool-loop-hang.md)
  - [worker-output-quality.md](./docs/known-issues/worker-output-quality.md)
- [docs/product-direction/](./docs/product-direction/) — strategic notes
  - [visibility-and-trust.md](./docs/product-direction/visibility-and-trust.md)
  - [git-integration.md](./docs/product-direction/git-integration.md)
  - [existing-project-integration.md](./docs/product-direction/existing-project-integration.md)

## License

MIT
