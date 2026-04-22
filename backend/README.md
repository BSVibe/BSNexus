# BSNexus Backend

FastAPI async monolith serving every BSNexus API. See the root
[CLAUDE.md](../CLAUDE.md) for the high-level architecture; this README
covers backend-specific commands and module pointers.

## Setup

```bash
cd backend
uv sync
```

## Running

```bash
uvicorn backend.src.main:app --host 0.0.0.0 --port 8000 --reload
```

The lifespan starts three background tasks:

- **`start_background_consumer`** — escalation stream consumer
- **`start_global_dispatcher`** — promotes pending tasks to running and
  advances completed phases (5s tick)
- **`start_channel_supervisor`** — reconciles `ProjectChannel` rows and
  runs one `ChannelFanout` per project (30s tick)

## API Endpoints

| Prefix                                          | Description                                          |
| ----------------------------------------------- | ---------------------------------------------------- |
| `/api/v1/projects`                              | Project and phase CRUD                               |
| `/api/v1/projects/import`                       | Import an existing codebase (local / git / tarball)  |
| `/api/v1/projects/{id}/chat`                    | Unified chat (DB-backed, fire-and-forget fan-out)    |
| `/api/v1/projects/{id}/chat/events`             | SSE: real-time chat message stream                   |
| `/api/v1/projects/{id}/plan-tree`               | Goal → Phase → Task tree for the Plan view           |
| `/api/v1/projects/{id}/plan-tree/events`        | SSE: task transitions, phase advances, agent status  |
| `/api/v1/projects/{id}/agent-status`            | Per-agent status cards (current task + dot color)    |
| `/api/v1/projects/{id}/design/system`           | DesignSystem `.bsd` workspace file (lazy-created)    |
| `/api/v1/projects/{id}/design/screens`          | Screen `.bsd` file CRUD                              |
| `/api/v1/projects/{id}/memories`                | Long-term memory (Local or BSage provider)           |
| `/api/v1/projects/{id}/channels`                | External chat channel links (Slack, ...)             |
| `/api/v1/tasks`                                 | Task CRUD and state transitions                      |
| `/api/v1/tasks/{id}/activity`                   | Task activity feed (milestone + tool log)            |
| `/api/v1/agents`                                | Agent CRUD + org chart                               |
| `/api/v1/agent-templates`                       | Predefined org chart templates (incl. specialists)   |
| `/api/v1/workers`                               | Worker registration, heartbeat, poll, result         |
| `/api/v1/goals`                                 | Goal CRUD (mission / department / project / task)    |
| `/api/v1/budget`                                | Per-agent budgets and cost records                   |
| `/api/v1/dashboard`                             | Project + task aggregates                            |
| `/api/v1/settings`                              | Global LLM + per-tenant install token                |
| `/api/v1/executor-configs`                      | Executor backend registrations                       |
| `/api/v1/mcp`                                   | MCP-style task / dependency endpoints                |
| `/health`, `/health/deps`                       | Health checks                                        |

Live OpenAPI docs: http://localhost:8000/docs

## Testing

```bash
# All tests with coverage gate (CI fails below 80%)
uv run pytest tests/ -v --cov=src --cov-fail-under=80

# Integration tests only
uv run pytest tests/integration/ -v

# Lint
uv run ruff check src/
```

## Database

```bash
# Run migrations
uv run alembic upgrade head

# Generate a new migration
uv run alembic revision -m "describe change"
```

Test DB is SQLite in-memory via `aiosqlite`; production is PostgreSQL.
The current head is the chain under `backend/alembic/versions/`.

## Key Modules

- **`api/plan_tree.py`** — Plan view: tree, agent status, SSE stream
- **`api/design.py`** — Workspace-backed `.bsd` design files
- **`api/import_project.py`** — Pluggable source/storage import flow
- **`api/channels.py`** — `ProjectChannel` CRUD
- **`api/memory.py`** — Long-term memory routes (provider auto-selected)
- **`core/state_machine.py`** — 4-state Task lifecycle, activity emission
- **`core/global_dispatcher.py`** — Background promotion + phase advance
- **`core/channel_adapter.py`** — `SlackChannelAdapter` + `ChannelFanout`
- **`core/channel_supervisor.py`** — Reconciler that runs fanouts in lifespan
- **`core/memory.py`** — `LocalMemoryProvider` + `BSageMemoryProvider` + factory
- **`core/tenant_context.py`** — JWT-derived tenant id middleware + dep
- **`core/import_sources.py`** — `LocalPathSource` / `GitRemoteSource` / `TarballSource`
- **`core/workspace_storage.py`** — `LocalWorkspaceStorage` / `GitWorkspaceStorage`
- **`core/workspace/service.py`** — `WorkspaceService` over `LocalStorageBackend`
- **`prompts/specialists.py`** — Designer / Analyzer / Planner / Memory Keeper system prompts
- **`queue/streams.py`** — Redis Streams consumer-group abstraction
