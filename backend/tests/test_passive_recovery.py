"""Tests for _recover_failed_passive_task — auto-heal passive agent errors."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.src.api.agent_chat import (
    PASSIVE_RECOVERY_MAX_RETRIES,
    _recover_failed_passive_task,
)
from backend.src.models import (
    Agent,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskHistory,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
    Tenant,
)

_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000020")


def _wrap_session(session):
    """Mimic `async with async_session() as db:` — share the test session so
    seeded FK rows are visible and committed writes are observable."""
    class _Wrap:
        def __init__(self, sess):
            self._sess = sess

        async def __aenter__(self):
            return self._sess

        async def __aexit__(self, *args):
            # Don't let the test session be closed out from under us; the
            # fixture owns its lifecycle.
            return False

    return lambda: _Wrap(session)


@pytest_asyncio.fixture
async def seeded(db_session, monkeypatch):
    monkeypatch.setattr(
        "backend.src.api.agent_chat.async_session", _wrap_session(db_session)
    )

    db_session.add(Tenant(id=_TENANT, name="T", slug="t", owner_user_id="u"))
    project = Project(id=uuid.uuid4(), name="P", description="d",
                      status=ProjectStatus.active, repo_path="/tmp/p")
    db_session.add(project)
    await db_session.flush()

    phase = Phase(id=uuid.uuid4(), project_id=project.id, name="Phase",
                  order=1, status=PhaseStatus.active, branch_name="main")
    db_session.add(phase)
    await db_session.flush()

    agent = Agent(id=uuid.uuid4(), tenant_id=_TENANT, name="Engineer",
                  role="engineer", title="E", job_description="j",
                  executor_type="generic_llm", executor_config={},
                  capabilities=["coding"], is_active=True)
    db_session.add(agent)
    await db_session.flush()

    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Do it", status=TaskStatus.running,
        priority=TaskPriority.medium, task_type=TaskType.feature,
        source=TaskSource.llm, assigned_agent_id=agent.id,
    )
    db_session.add(task)
    await db_session.commit()

    return {"project": project, "phase": phase, "agent": agent, "task": task, "db": db_session}


async def _task_status(db, task_id) -> str:
    # Force a fresh read — recovery committed from inside the same session
    # but SQLAlchemy may still hold a cached identity map.
    db.expire_all()
    t = await db.get(Task, task_id)
    assert t is not None, f"task {task_id} disappeared"
    return t.status.value if hasattr(t.status, "value") else str(t.status)


@pytest.mark.asyncio
async def test_timeout_error_requeues_task_to_pending(seeded):
    await _recover_failed_passive_task(
        project_id=seeded["project"].id,
        agent_id=seeded["agent"].id,
        task_id=seeded["task"].id,
        agent=seeded["agent"],
        error=Exception("litellm.Timeout: Connection timed out. Timeout passed=180.0"),
        redis=AsyncMock(),
    )
    status = await _task_status(seeded["db"], seeded["task"].id)
    assert status == "pending"


@pytest.mark.asyncio
async def test_connection_error_requeues_task(seeded):
    await _recover_failed_passive_task(
        project_id=seeded["project"].id,
        agent_id=seeded["agent"].id,
        task_id=seeded["task"].id,
        agent=seeded["agent"],
        error=Exception("ConnectionRefusedError: [Errno 61] connection refused"),
        redis=AsyncMock(),
    )
    status = await _task_status(seeded["db"], seeded["task"].id)
    assert status == "pending"


@pytest.mark.asyncio
async def test_unknown_error_still_requeues(seeded):
    await _recover_failed_passive_task(
        project_id=seeded["project"].id,
        agent_id=seeded["agent"].id,
        task_id=seeded["task"].id,
        agent=seeded["agent"],
        error=RuntimeError("tool handler crashed mid-call"),
        redis=AsyncMock(),
    )
    status = await _task_status(seeded["db"], seeded["task"].id)
    assert status == "pending"


@pytest.mark.asyncio
async def test_blocks_after_max_retries(seeded, db_session):
    # Pre-populate the history so we start at retry=N (maxed out)
    for _ in range(PASSIVE_RECOVERY_MAX_RETRIES):
        db_session.add(TaskHistory(
            task_id=seeded["task"].id,
            from_status=TaskStatus.running,
            to_status=TaskStatus.pending,
            actor="auto-recovery",
            reason="passive error: timeout — requeue (retry N)",
        ))
    await db_session.commit()

    await _recover_failed_passive_task(
        project_id=seeded["project"].id,
        agent_id=seeded["agent"].id,
        task_id=seeded["task"].id,
        agent=seeded["agent"],
        error=Exception("another timeout"),
        redis=AsyncMock(),
    )
    status = await _task_status(seeded["db"], seeded["task"].id)
    assert status == "blocked"


@pytest.mark.asyncio
async def test_friendly_chat_message_does_not_leak_raw_error(seeded):
    stream = AsyncMock()
    raw = "litellm.APIConnectionError: OllamaException - litellm.Timeout: Connection timed out. Timeout passed=180.0, time taken=180.012 seconds"
    await _recover_failed_passive_task(
        project_id=seeded["project"].id,
        agent_id=seeded["agent"].id,
        task_id=seeded["task"].id,
        agent=seeded["agent"],
        error=Exception(raw),
        redis=stream,
    )
    # The assistant message should be a friendly Korean summary,
    # not the raw Python exception.
    # Recovery commits inside the same shared session, so the message
    # should be visible via the same session's query path.
    from backend.src.repositories.conversation_repository import ConversationRepository
    repo = ConversationRepository(seeded["db"])
    msgs = await repo.list_by_project(seeded["project"].id, limit=5)
    assert msgs, "no conversation message written"
    content = msgs[-1].content
    assert "litellm" not in content
    assert "Traceback" not in content
    assert "Timeout passed" not in content
    assert "응답 지연" in content  # friendly 'timeout' label
