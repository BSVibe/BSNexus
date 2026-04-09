# BSNexus Backend

FastAPI async monolith serving all BSNexus APIs.

## Setup

```bash
cd backend
uv pip install -e ".[dev]"
```

## Running

```bash
uvicorn backend.src.main:app --host 0.0.0.0 --port 8000 --reload
```

## API Endpoints

| Prefix                              | Description                                  |
| ----------------------------------- | -------------------------------------------- |
| `/api/v1/projects`                  | Project and phase CRUD                       |
| `/api/v1/projects/{id}/chat`        | Unified project chat (DB-backed, SSE events) |
| `/api/v1/projects/{id}/chat/events` | SSE: real-time chat message stream           |
| `/api/v1/tasks`                     | Task CRUD and state transitions              |
| `/api/v1/agents`                    | Agent CRUD + org chart                       |
| `/api/v1/workers`                   | Worker registration, heartbeat, poll, result |
| `/api/v1/board`                     | Kanban board state and events                |
| `/api/v1/architect`                 | Design session chat (HTTP + WebSocket)       |
| `/api/v1/pm`                        | PM orchestration control                     |
| `/health`                           | Health check endpoints                       |

API docs: http://localhost:8000/docs

## Testing

```bash
# All tests with coverage
python -m pytest tests/ -v --cov=src --cov-fail-under=80

# Integration tests only
python -m pytest tests/integration/ -v

# Lint
ruff check src/
```

## Key Modules

- **`core/state_machine.py`** - Task state machine with dependency resolution
- **`core/orchestrator.py`** - PM orchestration logic
- **`queue/streams.py`** - Redis Streams consumer group management
- **`storage/database.py`** - Async SQLAlchemy session factory
