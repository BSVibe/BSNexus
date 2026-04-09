# CLAUDE.md

Project instructions for Claude Code when working on BSNexus.

## Project Overview

BSNexus is an AI-powered development management system. An LLM Architect designs projects through conversation, and distributed Worker nodes automatically write code.

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
backend/src/           # FastAPI backend
  api/                 # Route handlers
  core/                # Business logic (state machine, orchestrator, git ops)
  storage/             # Database & Redis clients
  queue/               # Redis Streams management
  repositories/        # Data access layer
  models.py            # SQLAlchemy models
  schemas.py           # Pydantic request/response schemas
  main.py              # App entry point

frontend/src/          # React frontend
  api/                 # Axios API clients
  components/          # UI components (layout, architect, board, workers)
  hooks/               # Custom hooks (useWebSocket, useSSE, useBoard)
  pages/               # Page components
  stores/              # Zustand stores
  types/               # TypeScript types (mirror backend schemas)

worker/src/            # Distributed worker agent
```

## Development Commands

```bash
# Backend
uvicorn backend.src.main:app --host 0.0.0.0 --port 8000 --reload
python -m pytest backend/tests/ -v --cov=backend/src --cov-fail-under=80
ruff check backend/src/

# Frontend
cd frontend && pnpm dev          # Dev server on port 3000
cd frontend && pnpm lint         # ESLint
cd frontend && pnpm build        # Production build

# Infrastructure
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis
```

## MUST Rules

- **Async everywhere**: Use `async/await` for all I/O. No synchronous blocking calls.
- **Type hints**: On all functions and methods.
- **Pydantic models**: For all request/response schemas.
- **Redis Streams**: Use consumer group pattern with JSON serialization and `XACK`. Never use Redis Pub/Sub.
- **LiteLLM only**: All LLM calls go through LiteLLM. Never import provider SDKs directly (no `openai`, no `anthropic`).
- **Dependency Injection**: Use FastAPI `Depends()` for DB sessions, Redis, etc.
- **Decimal for money**: Use `Decimal` type, never `float`.
- **Env vars for secrets**: Validate with Pydantic BaseSettings. Document in `.env.example`.
- **Tests required**: All code must have tests. Minimum 80% coverage.

## NEVER Rules

- Never use `sys.path.insert` or `sys.path.append`
- Never use `requirements.txt` — use `pyproject.toml` + `uv`
- Never hardcode secrets or API keys in code
- Never use f-strings in raw SQL queries
- Never include `Co-Authored-By` in commit messages
- Never use `float` for monetary values
- Never use synchronous blocking I/O
- Never use gRPC, Redpanda, or Kong Gateway (legacy stack)

## API Endpoints

| Prefix                              | Description                                   |
| ----------------------------------- | --------------------------------------------- |
| `/api/v1/projects`                  | Project and phase CRUD                        |
| `/api/v1/projects/{id}/chat`        | Unified project chat (DB-backed, SSE events)  |
| `/api/v1/projects/{id}/chat/events` | SSE: real-time chat message stream            |
| `/api/v1/tasks`                     | Task CRUD and state transitions               |
| `/api/v1/agents`                    | Agent CRUD + org chart                        |
| `/api/v1/workers`                   | Worker registration, heartbeat, poll, result  |
| `/api/v1/board`                     | Kanban board state and events                 |
| `/api/v1/architect`                 | Design session chat (HTTP + WebSocket)        |
| `/api/v1/pm`                        | PM orchestration control                      |

## Project Chat Architecture

The unified project chat is **source-agnostic** so it can fan out to web, Slack,
or any other channel via adapter without changing the core flow.

- **Persistence**: `conversation_messages` table (one row per message). Fields
  `source`, `external_id`, `thread_ref` let adapters round-trip with external
  systems (Slack ts, Discord message id, etc.). Repository:
  `backend/src/repositories/conversation_repository.py`.
- **Routing**: Mentions are parsed from the user message and matched against
  agent names. With no mention, `_pick_default_agent` scores each agent by
  `routing_keywords` (3x weight) plus `job_description`/`capabilities` token
  overlap, falling back to the org-chart root.
- **Delegation chain**: After the synchronously called agents respond, any
  `@mentions` in their replies are queued as a background `asyncio.Task` that
  uses a fresh DB session. This keeps long chains from blocking the HTTP
  response.
- **Event bus**: Every persisted message is published to the Redis Stream
  `chat:events:{project_id}` (`event=message_created`). Clear publishes
  `event=history_cleared`. Streams (not Pub/Sub) so adapters can use consumer
  groups for delivery guarantees if needed; the SSE handler tails with
  `XREAD $` for fan-out.
- **Frontend**: `useChatEvents(projectId)` opens an `EventSource` and patches
  the React Query cache directly. No polling.
- **Slack roadmap**: A `SlackChannelAdapter` will tail the same chat events
  stream and post via Slack Web API; an inbound webhook handler will call the
  same internal flow as the web POST endpoint.

## Task State Machine

```
waiting → ready → queued → in_progress → review → done
                                       ↘ rejected → ready (retry)
waiting/ready → blocked → ready (unblock)
```

## Testing Patterns

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- **Test DB**: SQLite in-memory (via `aiosqlite`)
- **Redis mock**: `AsyncMock` for stream operations
- **Fixtures**: `db_session`, `mock_stream_manager`, `client` (see `backend/tests/conftest.py`)
- **Integration tests**: `backend/tests/integration/`

## Commit Style

Conventional Commits: `feat(scope):`, `fix(scope):`, `test(scope):`, `docs(scope):`
No `Co-Authored-By` lines.
