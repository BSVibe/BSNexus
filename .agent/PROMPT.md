You are working on BSNexus, an AI-powered development management system.

## Your Task

Read `.agent/tasks.json` to find the highest-priority incomplete task (passes: false, lowest priority number). Implement ONLY that one task, then update tasks.json to mark it as passes: true.

## Project Context

- **Stack**: Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), Alembic, Redis Streams, structlog, pydantic-settings
- **Package manager**: uv (run commands with `uv run`)
- **Linting**: ruff (line-length 120)
- **Testing**: pytest + pytest-asyncio, asyncio_mode = "auto", coverage >= 80%

## Key Files

- `backend/src/providers/` — GatewayProvider, SupervisorProvider, KnowledgeProvider (already implemented)
- `backend/src/core/executor/` — ExecutorProtocol and registry (already implemented)
- `backend/src/models.py` — SQLAlchemy models (Task model exists here)
- `backend/src/schemas.py` — Pydantic schemas
- `backend/src/api/` — FastAPI route handlers
- `backend/src/main.py` — FastAPI app entry point
- `backend/tests/` — Test directory

## Design

- **TaskSuggestion** and **Task** are SEPARATE models. Approval converts a suggestion to a task.
- **PlannerService** uses KnowledgeProvider (for SOT/SOP context) and GatewayProvider (for LLM calls)
- All LLM calls go through GatewayProvider (not direct litellm)

## Rules

1. **TDD**: Write failing tests BEFORE implementation code
2. **No Co-Authored-By** in commit messages
3. **structlog** for logging, **pydantic-settings** for config
4. **async/await** for all I/O
5. Run `uv run ruff check backend/src/` to verify lint
6. Run `uv run pytest backend/tests/ -v --cov=backend/src --cov-fail-under=80` to verify tests
7. Commit with format: `type(scope): description`

## After completing the task

Update `.agent/tasks.json`: set the completed task's `passes` to `true`. Append findings to `.agent/progress.txt`.
