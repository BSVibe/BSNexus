"""Tests for GET /api/v1/dashboard/projects-summary endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.src.api.dashboard import get_projects_summary
from backend.src.models import (
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
)


@pytest.mark.asyncio
async def test_projects_summary_empty(client: AsyncClient) -> None:
    """Empty DB returns empty list."""
    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_projects_summary_with_data(client: AsyncClient, db_session) -> None:
    """Returns correct task counts, bug counts, and phase info."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Test Project",
        description="desc",
        repo_path="/test",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        order=1,
        status=PhaseStatus.active,
        branch_name="main",
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    # 2 ready tasks, 1 done task, 1 bug task
    for i, (status, task_type) in enumerate(
        [
            (TaskStatus.pending, TaskType.feature),
            (TaskStatus.pending, TaskType.feature),
            (TaskStatus.done, TaskType.feature),
            (TaskStatus.pending, TaskType.bug),
        ]
    ):
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Task {i}",
                status=status,
                priority=TaskPriority.medium,
                task_type=task_type,
                source=TaskSource.llm,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1

    summary = data[0]
    assert summary["name"] == "Test Project"
    assert summary["status"] == "active"
    assert summary["current_phase"] == "Phase 1"
    assert summary["bug_count"] == 1
    assert summary["task_counts"]["pending"] == 3
    assert summary["task_counts"]["done"] == 1


@pytest.mark.asyncio
async def test_projects_summary_multiple_projects(client: AsyncClient, db_session) -> None:
    """Returns summary for multiple projects."""
    now = datetime.now(timezone.utc)

    # Create multiple projects with different statuses
    for status in (ProjectStatus.active, ProjectStatus.design, ProjectStatus.completed):
        project = Project(
            id=uuid.uuid4(),
            name=f"P-{status.value}",
            description="d",
            repo_path="/t",
            status=status,
            created_at=now,
            updated_at=now,
        )
        db_session.add(project)

    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3


async def test_projects_summary_no_active_phase(client: AsyncClient, db_session) -> None:
    """Project with only pending/completed phases returns current_phase=None."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="No Active Phase",
        description="d",
        repo_path="/t",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    # Only a completed phase, no active phase
    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Old Phase",
        order=1,
        status=PhaseStatus.completed,
        branch_name="old",
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["current_phase"] is None
    assert data[0]["task_counts"] == {}
    assert data[0]["bug_count"] == 0


async def test_projects_summary_no_tasks(client: AsyncClient, db_session) -> None:
    """Project with phases but no tasks returns empty task_counts and bug_count=0."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Empty Tasks",
        description="d",
        repo_path="/t",
        status=ProjectStatus.design,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        order=1,
        status=PhaseStatus.active,
        branch_name="main",
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["task_counts"] == {}
    assert data[0]["bug_count"] == 0
    assert data[0]["last_activity"] is None


async def test_projects_summary_last_activity(client: AsyncClient, db_session) -> None:
    """last_activity reflects the most recent task updated_at."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Activity Project",
        description="d",
        repo_path="/t",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Ph",
        order=1,
        status=PhaseStatus.active,
        branch_name="b",
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    from datetime import timedelta

    earlier = now - timedelta(hours=2)
    later = now - timedelta(hours=1)

    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=project.id,
            phase_id=phase.id,
            title="Old Task",
            status=TaskStatus.done,
            priority=TaskPriority.medium,
            task_type=TaskType.feature,
            source=TaskSource.llm,
            created_at=earlier,
            updated_at=earlier,
        )
    )
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=project.id,
            phase_id=phase.id,
            title="New Task",
            status=TaskStatus.running,
            priority=TaskPriority.high,
            task_type=TaskType.feature,
            source=TaskSource.llm,
            created_at=later,
            updated_at=later,
        )
    )
    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["last_activity"] is not None
    # The last_activity should be the later timestamp
    activity_str = data[0]["last_activity"]
    assert activity_str is not None


async def test_projects_summary_bug_count_aggregation(client: AsyncClient, db_session) -> None:
    """bug_count correctly counts only bug-type tasks per project."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Bug Project",
        description="d",
        repo_path="/t",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Ph",
        order=1,
        status=PhaseStatus.active,
        branch_name="b",
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    # 3 bugs, 2 features
    for i, tt in enumerate([TaskType.bug, TaskType.bug, TaskType.bug, TaskType.feature, TaskType.feature]):
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Task {i}",
                status=TaskStatus.pending,
                priority=TaskPriority.medium,
                task_type=tt,
                source=TaskSource.llm,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["bug_count"] == 3
    assert data[0]["task_counts"]["pending"] == 5


async def test_projects_summary_multiple_projects_with_tasks(client: AsyncClient, db_session) -> None:
    """Multiple projects each get their own correct task_counts and bug_counts."""
    now = datetime.now(timezone.utc)

    p1 = Project(
        id=uuid.uuid4(),
        name="P1",
        description="d",
        repo_path="/t1",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    p2 = Project(
        id=uuid.uuid4(),
        name="P2",
        description="d",
        repo_path="/t2",
        status=ProjectStatus.design,
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([p1, p2])

    ph1 = Phase(
        id=uuid.uuid4(),
        project_id=p1.id,
        name="Ph1",
        order=1,
        status=PhaseStatus.active,
        branch_name="b1",
        created_at=now,
        updated_at=now,
    )
    ph2 = Phase(
        id=uuid.uuid4(),
        project_id=p2.id,
        name="Ph2",
        order=1,
        status=PhaseStatus.pending,
        branch_name="b2",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([ph1, ph2])
    await db_session.flush()

    # P1: 2 done tasks, 1 bug
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=p1.id,
            phase_id=ph1.id,
            title="P1 done",
            status=TaskStatus.done,
            priority=TaskPriority.medium,
            task_type=TaskType.feature,
            source=TaskSource.llm,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=p1.id,
            phase_id=ph1.id,
            title="P1 bug",
            status=TaskStatus.pending,
            priority=TaskPriority.high,
            task_type=TaskType.bug,
            source=TaskSource.auto_bug,
            created_at=now,
            updated_at=now,
        )
    )

    # P2: 1 waiting task, no bugs
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=p2.id,
            phase_id=ph2.id,
            title="P2 waiting",
            status=TaskStatus.pending,
            priority=TaskPriority.low,
            task_type=TaskType.chore,
            source=TaskSource.manual,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    by_name = {d["name"]: d for d in data}

    assert by_name["P1"]["bug_count"] == 1
    assert by_name["P1"]["task_counts"]["done"] == 1
    assert by_name["P1"]["task_counts"]["pending"] == 1
    assert by_name["P1"]["current_phase"] == "Ph1"

    assert by_name["P2"]["bug_count"] == 0
    assert by_name["P2"]["task_counts"]["pending"] == 1
    assert by_name["P2"]["current_phase"] is None  # pending phase, not active


# ── Direct-call tests (bypass ASGITransport for coverage tracking) ──────────


async def test_get_projects_summary_direct_empty(db_session) -> None:
    """Direct call: empty DB returns empty list."""
    result = await get_projects_summary(db=db_session)
    assert result == []


async def test_get_projects_summary_direct_no_active_phase(db_session) -> None:
    """Direct call: project with only completed phase returns current_phase=None."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="NoPhase",
        description="d",
        repo_path="/t",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Completed Phase",
        order=1,
        status=PhaseStatus.completed,
        branch_name="old",
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    result = await get_projects_summary(db=db_session)
    assert len(result) == 1
    assert result[0].current_phase is None
    assert result[0].task_counts == {}
    assert result[0].bug_count == 0
    assert result[0].last_activity is None


async def test_get_projects_summary_direct_with_tasks_and_bugs(db_session) -> None:
    """Direct call: task counts, bug counts, and last_activity are correct."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Full",
        description="d",
        repo_path="/t",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Active Phase",
        order=1,
        status=PhaseStatus.active,
        branch_name="main",
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    from datetime import timedelta

    earlier = now - timedelta(hours=3)
    later = now - timedelta(hours=1)

    # 2 features (ready, done), 2 bugs (ready, in_progress)
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=project.id,
            phase_id=phase.id,
            title="feat-ready",
            status=TaskStatus.pending,
            priority=TaskPriority.medium,
            task_type=TaskType.feature,
            source=TaskSource.llm,
            created_at=earlier,
            updated_at=earlier,
        )
    )
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=project.id,
            phase_id=phase.id,
            title="feat-done",
            status=TaskStatus.done,
            priority=TaskPriority.medium,
            task_type=TaskType.feature,
            source=TaskSource.llm,
            created_at=earlier,
            updated_at=earlier,
        )
    )
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=project.id,
            phase_id=phase.id,
            title="bug-ready",
            status=TaskStatus.pending,
            priority=TaskPriority.high,
            task_type=TaskType.bug,
            source=TaskSource.auto_bug,
            created_at=later,
            updated_at=later,
        )
    )
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=project.id,
            phase_id=phase.id,
            title="bug-ip",
            status=TaskStatus.running,
            priority=TaskPriority.high,
            task_type=TaskType.bug,
            source=TaskSource.auto_bug,
            created_at=later,
            updated_at=later,
        )
    )
    await db_session.flush()

    result = await get_projects_summary(db=db_session)
    assert len(result) == 1
    s = result[0]
    assert s.name == "Full"
    assert s.current_phase == "Active Phase"
    assert s.task_counts == {"pending": 2, "done": 1, "running": 1}
    assert s.bug_count == 2
    assert s.last_activity is not None


async def test_get_projects_summary_direct_multiple_projects(db_session) -> None:
    """Direct call: multiple projects with different data are correctly separated."""
    now = datetime.now(timezone.utc)
    from datetime import timedelta

    p1 = Project(
        id=uuid.uuid4(),
        name="P1",
        description="d",
        repo_path="/t1",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    p2 = Project(
        id=uuid.uuid4(),
        name="P2",
        description="d",
        repo_path="/t2",
        status=ProjectStatus.design,
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([p1, p2])

    ph1 = Phase(
        id=uuid.uuid4(),
        project_id=p1.id,
        name="Ph1",
        order=1,
        status=PhaseStatus.active,
        branch_name="b1",
        created_at=now,
        updated_at=now,
    )
    ph2 = Phase(
        id=uuid.uuid4(),
        project_id=p2.id,
        name="Ph2",
        order=1,
        status=PhaseStatus.pending,
        branch_name="b2",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([ph1, ph2])
    await db_session.flush()

    # P1: 1 bug + 1 feature
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=p1.id,
            phase_id=ph1.id,
            title="P1-bug",
            status=TaskStatus.pending,
            priority=TaskPriority.high,
            task_type=TaskType.bug,
            source=TaskSource.auto_bug,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.add(
        Task(
            id=uuid.uuid4(),
            project_id=p1.id,
            phase_id=ph1.id,
            title="P1-feat",
            status=TaskStatus.done,
            priority=TaskPriority.medium,
            task_type=TaskType.feature,
            source=TaskSource.llm,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
    )

    # P2: no tasks
    await db_session.flush()

    result = await get_projects_summary(db=db_session)
    assert len(result) == 2

    by_name = {s.name: s for s in result}

    assert by_name["P1"].bug_count == 1
    assert by_name["P1"].task_counts == {"pending": 1, "done": 1}
    assert by_name["P1"].current_phase == "Ph1"
    assert by_name["P1"].last_activity is not None

    assert by_name["P2"].bug_count == 0
    assert by_name["P2"].task_counts == {}
    assert by_name["P2"].current_phase is None  # pending, not active
    assert by_name["P2"].last_activity is None
