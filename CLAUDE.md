# CLAUDE.md

Project instructions for Claude Code when working on BSNexus.

## Project Overview

BSNexus is an AI Company OS. Users hire AI agents (Designer, Analyzer,
Planner, Engineer, QA, ...) into an org chart, give them goals, and the
agents collaborate via a unified chat to plan and execute work. A
distributed worker pool runs the heavy code-execution tasks; the
backend orchestrates dispatch, dependency promotion, and phase
advancement on a single global loop.

There is no separate "Architect" agent or "PM Orchestrator" — every
agent is a normal `Agent` row with a tailored `system_prompt`, and
every task flows through the same worker dispatch pipeline.

## Core Stack

- **Python 3.11+** / **FastAPI** (async monolith)
- **PostgreSQL 16** + SQLAlchemy 2.0 (async) + Alembic
- **Redis Streams** (consumer groups, NOT Pub/Sub)
- **LiteLLM** (provider-agnostic LLM integration)
- **React 19** + TypeScript + Vite + Tailwind CSS
- **Package managers**: `uv` (Python), `pnpm` (Node.js)
- **Linting**: `ruff` (line-length 120)

## Project Structure

```
backend/src/
  api/                   # Route handlers (one router per resource)
  core/                  # Business logic
    state_machine.py     # 4-state Task lifecycle + activity emission
    global_dispatcher.py # Background loop: pending -> running, phase advance
    channel_adapter.py   # Slack/Discord fan-out adapters
    channel_supervisor.py# Lifespan task that runs one fanout per project
    memory.py            # Local + BSage memory providers
    tenant_context.py    # JWT-derived tenant id middleware
    workspace/           # Per-project workspace storage
    workspace_storage.py # Pluggable storage backends (local, git, ...)
    import_sources.py    # Pluggable source providers (local, git, tarball)
    task_markers.py      # CREATE_TASK / SET_GOAL marker parsers
  prompts/
    specialists.py       # Designer / Analyzer / Planner / Memory Keeper prompts
    review.yaml          # QA review prompts
  models/                # SQLAlchemy models, one file per domain
  schemas/               # Pydantic request/response schemas
  repositories/          # Data access layer
  alembic/versions/      # DB migrations
  storage/               # Database + Redis clients
  queue/streams.py       # Redis Streams abstraction
  main.py                # App entry point + lifespan

frontend/src/
  api/                   # Axios clients per resource
  components/
    plan/                # PlanTree, AgentStatusBar, DetailPanel, PlanView
    project/             # Chat sidebar, channels modal, design view, ...
    common/              # Modal, Button, etc.
  hooks/                 # useChatEvents, usePlanEvents
  pages/                 # ProjectPage, AgentsPage, DashboardPage, ...
  stores/                # Zustand: planStore, agentStore, toastStore
  types/                 # TypeScript types

worker/src/              # Distributed worker agent
```

## Development Commands

```bash
# Backend
uvicorn backend.src.main:app --host 0.0.0.0 --port 8000 --reload
uv run --project backend pytest backend/tests/ -v --cov=backend/src --cov-fail-under=80
uv run --project backend ruff check backend/src/

# Frontend
cd frontend && pnpm dev          # Dev server on port 3000
cd frontend && pnpm lint         # ESLint
cd frontend && pnpm exec tsc -b  # Type-check
cd frontend && pnpm build        # Production build

# E2E
cd frontend && pnpm test:e2e     # Playwright (mock-API mode)

# Infrastructure
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis
```

## MUST Rules

- **Async everywhere**: `async/await` for all I/O. No synchronous blocking.
- **Type hints**: On all functions and methods.
- **Pydantic models**: For all request/response schemas.
- **Redis Streams**: Consumer group pattern with JSON serialization and
  `XACK`. Never use Redis Pub/Sub.
- **LiteLLM only**: All LLM calls go through LiteLLM. No direct
  `openai`/`anthropic` SDK imports.
- **Tenant scoping**: Routes that mutate data take
  `tenant_id: uuid.UUID = Depends(get_tenant_id)` and pass it through
  every closure. Don't fall back to `DEFAULT_TENANT_ID` outside of
  install-token / unauthenticated worker paths.
- **Dependency Injection**: Use FastAPI `Depends()` for DB sessions,
  Redis, tenant id, etc.
- **Decimal for money**: Use `Decimal`, never `float`.
- **Env vars for secrets**: Validate with Pydantic BaseSettings.
  Document in `.env.example`.
- **Tests required**: All code must have tests. Minimum 80% coverage
  enforced by CI.

## NEVER Rules

- Never use `sys.path.insert` or `sys.path.append`
- Never use `requirements.txt` — use `pyproject.toml` + `uv`
- Never hardcode secrets or API keys in code
- Never use f-strings in raw SQL queries
- Never include `Co-Authored-By` in commit messages
- Never use `float` for monetary values
- Never use synchronous blocking I/O
- Never reintroduce a static "Architect" or "PM Orchestrator" — agents
  do that work via `system_prompt` + worker dispatch
- Never store project assets (designs, plans, screens) in DB tables
  when a workspace file would do — `.bsd` files for designs are the
  template
- Never bypass `state_machine.transition()` for task status changes —
  it writes both `TaskHistory` and `TaskActivity` rows and emits the
  Plan SSE event

## API Endpoints

| Prefix                                          | Description                                              |
| ----------------------------------------------- | -------------------------------------------------------- |
| `/api/v1/projects`                              | Project and phase CRUD                                   |
| `/api/v1/projects/{id}/chat`                    | Unified project chat (DB-backed, fire-and-forget)        |
| `/api/v1/projects/{id}/chat/events`             | SSE: real-time chat message stream                       |
| `/api/v1/projects/{id}/plan-tree`               | Goal → Phase → Task tree for the Plan view               |
| `/api/v1/projects/{id}/plan-tree/events`        | SSE: task transitions, phase advances, agent status      |
| `/api/v1/projects/{id}/agent-status`            | Agent status cards (current task + dot color)            |
| `/api/v1/projects/{id}/design/system`           | DesignSystem `.bsd` file (lazy-created)                  |
| `/api/v1/projects/{id}/design/screens`          | Screen `.bsd` file CRUD                                  |
| `/api/v1/projects/{id}/memories`                | Long-term memory (Local or BSage provider)               |
| `/api/v1/projects/{id}/channels`                | External chat channel links (Slack, ...)                 |
| `/api/v1/projects/import`                       | Import an existing codebase (local/git/tarball)          |
| `/api/v1/tasks`                                 | Task CRUD and state transitions                          |
| `/api/v1/tasks/{id}/activity`                   | Task activity feed (milestone + tool log)                |
| `/api/v1/agents`                                | Agent CRUD + org chart                                   |
| `/api/v1/agent-templates`                       | Predefined org chart templates (incl. specialists)       |
| `/api/v1/workers`                               | Worker registration, heartbeat, poll, result             |
| `/api/v1/goals`                                 | Goal CRUD (mission, department, project, task levels)   |
| `/api/v1/budget`                                | Per-agent budgets and cost records                       |
| `/api/v1/dashboard`                             | Project + task aggregates                                |
| `/api/v1/settings`                              | Global LLM + per-tenant install token                    |
| `/api/v1/executor-configs`                      | Executor backend registrations                           |
| `/api/v1/mcp`                                   | MCP-style task/dependency endpoints                      |

## Plan View Architecture

The Plan view replaces the old Kanban board. It is the primary lens
on what every agent is doing right now.

- **Backend**: `plan_tree.py` returns a `Goal → Phases → Tasks` tree.
  `state_machine.transition()` publishes a `task_transition` event
  to `project:events:{project_id}` on every status change. The global
  dispatcher publishes `phase_advanced` when an active phase
  completes.
- **Frontend**: `PlanView` lays out the agent status bar (top),
  `PlanTree` (left, 35%), and `DetailPanel` (right). `usePlanEvents`
  subscribes to the SSE stream and patches the React Query cache
  directly.
- **Status model**: 4 states only — `pending`, `running`, `blocked`,
  `done`. The 6-state Kanban legacy was deleted.

## Project Chat Architecture

The unified project chat is **source-agnostic** so it can fan out to
web, Slack, or any other channel via `ChannelFanout` without changing
the core flow.

- **Persistence**: `conversation_messages` table. Fields `source`,
  `external_id`, `thread_ref` let adapters round-trip with external
  systems (Slack ts, Discord message id, etc.).
- **Routing**: `@mentions` are parsed and matched against agent names.
  With no mention, the org-chart root's worker chooses the right agent
  via a one-shot LLM call. The static `routing_keywords` field is gone.
- **Delegation chain**: Background `asyncio.Task` per agent — fresh DB
  session, fresh tenant context. Cascading mentions are dispatched
  recursively up to `MAX_DELEGATION_DEPTH`.
- **Event bus**: Every persisted message is published to the Redis
  Stream `chat:events:{project_id}`.
- **Org-mission injection**: Every system prompt prepends the active
  tenant's `Goal.level == "mission"` rows so cross-session context is
  stable. Long-term memories are still stored via `/memories`, but
  injection is mission-only — no per-turn N-of-recent dump.

## Channel Fan-out

- **Per-project rows**: `ProjectChannel` (kind, external_channel_id,
  credentials_encrypted, is_active).
- **Adapters**: `core/channel_adapter.py` defines `ChannelAdapter`
  Protocol + `SlackChannelAdapter`. Adding Discord/Teams is a one-class
  change plus a registry entry.
- **Supervisor**: `ChannelSupervisor` runs in the FastAPI lifespan,
  reconciles `ProjectChannel` rows every 30s, and starts/stops one
  `ChannelFanout` task per project. Each fanout tails
  `chat:events:{project_id}` and posts new messages to every active
  channel.

## Task State Machine

```
pending → running → done
   ↑        ↓
   ╰────────┤
            ↓
         blocked → pending
```

- `pending`: not yet started or waiting on dependencies
- `running`: actively being worked on (worker dispatched)
- `blocked`: stuck — needs human intervention or replan
- `done`: completed (terminal)

State changes go through `state_machine.transition()`, which writes a
`TaskHistory` row, a milestone `TaskActivity` row, and publishes a
`task_transition` event to the per-project plan event stream.

## Tenant Model

- **Personal tenants**: A user without a `tenant_id` claim in the JWT
  gets a deterministic UUIDv5 personal tenant id derived from their
  user id. `ensure_personal_tenant` lazy-creates the row on first
  authenticated request.
- **Middleware**: `TenantMiddleware` decodes the JWT (no signature
  check — that happens in the auth dependency) and stamps
  `request.state.tenant_id`. Routes consume it via
  `Depends(get_tenant_id)`.
- **Worker install tokens**: Per tenant, stored on
  `Tenant.worker_install_token_hash`. Workers register with the token
  and inherit the tenant. Open-mode (no token at all) only works when
  the default tenant has not minted one — single-tenant dev preserved.

## Testing Patterns

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- **Test DB**: SQLite in-memory (via `aiosqlite`)
- **Redis mock**: `AsyncMock` for stream operations. **Always set
  `tail.return_value = []` and yield with `asyncio.sleep(0)` in
  polling loops** — busy AsyncMock loops hang otherwise.
- **Fixtures**: `db_session`, `mock_stream_manager`, `client` (see
  `backend/tests/conftest.py`)
- **Integration tests**: `backend/tests/integration/`
- **Coverage gate**: 80% (CI fails below).

## Commit Style

Conventional Commits: `feat(scope):`, `fix(scope):`, `test(scope):`,
`docs(scope):`. No `Co-Authored-By` lines.
