"""Tests for TaskSuggestion model and Pydantic schemas (TASK-001)."""
import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Project, ProjectStatus, TaskSuggestion, SuggestionStatus
from backend.src.schemas import (
    TaskSuggestionCreate,
    TaskSuggestionResponse,
    TaskSuggestionUpdate,
    SuggestionStatus as SchemaSuggestionStatus,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def project(db_session: AsyncSession) -> Project:
    """Create a test project for FK references."""
    proj = Project(
        name="Test Project",
        description="A test project",
        repo_path="/tmp/test-repo",
        status=ProjectStatus.active,
    )
    db_session.add(proj)
    await db_session.commit()
    await db_session.refresh(proj)
    return proj


# ── Model: SuggestionStatus Enum ─────────────────────────────────────


class TestSuggestionStatusEnum:
    def test_has_pending(self):
        assert SuggestionStatus.pending.value == "pending"

    def test_has_approved(self):
        assert SuggestionStatus.approved.value == "approved"

    def test_has_rejected(self):
        assert SuggestionStatus.rejected.value == "rejected"

    def test_has_modified(self):
        assert SuggestionStatus.modified.value == "modified"


# ── Model: CRUD ──────────────────────────────────────────────────────


class TestTaskSuggestionModel:
    async def test_create_suggestion(self, db_session: AsyncSession, project: Project):
        suggestion = TaskSuggestion(
            project_id=project.id,
            title="Add login page",
            description="Implement user authentication flow",
            task_type="feature",
            priority=1,
            estimated_effort="2h",
            reasoning="Users need to log in",
        )
        db_session.add(suggestion)
        await db_session.commit()
        await db_session.refresh(suggestion)

        assert suggestion.id is not None
        assert suggestion.project_id == project.id
        assert suggestion.title == "Add login page"
        assert suggestion.description == "Implement user authentication flow"
        assert suggestion.task_type == "feature"
        assert suggestion.priority == 1
        assert suggestion.estimated_effort == "2h"
        assert suggestion.reasoning == "Users need to log in"
        assert suggestion.status == SuggestionStatus.pending
        assert suggestion.created_at is not None
        assert suggestion.updated_at is not None

    async def test_default_status_is_pending(self, db_session: AsyncSession, project: Project):
        suggestion = TaskSuggestion(
            project_id=project.id,
            title="Default status test",
            description="Should default to pending",
            task_type="bug",
            priority=2,
        )
        db_session.add(suggestion)
        await db_session.commit()
        await db_session.refresh(suggestion)

        assert suggestion.status == SuggestionStatus.pending

    async def test_optional_fields_are_nullable(self, db_session: AsyncSession, project: Project):
        suggestion = TaskSuggestion(
            project_id=project.id,
            title="Minimal suggestion",
            task_type="chore",
            priority=3,
        )
        db_session.add(suggestion)
        await db_session.commit()
        await db_session.refresh(suggestion)

        assert suggestion.description is None
        assert suggestion.estimated_effort is None
        assert suggestion.reasoning is None
        assert suggestion.rejection_reason is None

    async def test_read_suggestion(self, db_session: AsyncSession, project: Project):
        suggestion = TaskSuggestion(
            project_id=project.id,
            title="Read test",
            task_type="feature",
            priority=1,
        )
        db_session.add(suggestion)
        await db_session.commit()

        result = await db_session.execute(select(TaskSuggestion).where(TaskSuggestion.id == suggestion.id))
        fetched = result.scalar_one()
        assert fetched.title == "Read test"

    async def test_update_suggestion_status(self, db_session: AsyncSession, project: Project):
        suggestion = TaskSuggestion(
            project_id=project.id,
            title="Update test",
            task_type="improvement",
            priority=2,
        )
        db_session.add(suggestion)
        await db_session.commit()

        suggestion.status = SuggestionStatus.approved
        await db_session.commit()
        await db_session.refresh(suggestion)

        assert suggestion.status == SuggestionStatus.approved

    async def test_delete_suggestion(self, db_session: AsyncSession, project: Project):
        suggestion = TaskSuggestion(
            project_id=project.id,
            title="Delete test",
            task_type="test",
            priority=3,
        )
        db_session.add(suggestion)
        await db_session.commit()
        sid = suggestion.id

        await db_session.delete(suggestion)
        await db_session.commit()

        result = await db_session.execute(select(TaskSuggestion).where(TaskSuggestion.id == sid))
        assert result.scalar_one_or_none() is None

    async def test_rejection_reason_stored(self, db_session: AsyncSession, project: Project):
        suggestion = TaskSuggestion(
            project_id=project.id,
            title="Reject test",
            task_type="feature",
            priority=1,
        )
        db_session.add(suggestion)
        await db_session.commit()

        suggestion.status = SuggestionStatus.rejected
        suggestion.rejection_reason = "Not aligned with sprint goals"
        await db_session.commit()
        await db_session.refresh(suggestion)

        assert suggestion.rejection_reason == "Not aligned with sprint goals"

    async def test_project_fk_constraint(self, db_session: AsyncSession):
        """TaskSuggestion with non-existent project_id should fail."""
        suggestion = TaskSuggestion(
            project_id=uuid.uuid4(),
            title="Orphan suggestion",
            task_type="feature",
            priority=1,
        )
        db_session.add(suggestion)
        with pytest.raises(Exception):
            await db_session.commit()


# ── Schema: TaskSuggestionCreate ─────────────────────────────────────


class TestTaskSuggestionCreateSchema:
    def test_valid_create(self):
        data = TaskSuggestionCreate(
            project_id=uuid.uuid4(),
            title="New feature",
            description="Implement it",
            task_type="feature",
            priority=1,
            estimated_effort="4h",
            reasoning="Important for users",
        )
        assert data.title == "New feature"
        assert data.priority == 1

    def test_minimal_create(self):
        data = TaskSuggestionCreate(
            project_id=uuid.uuid4(),
            title="Minimal",
            task_type="bug",
            priority=2,
        )
        assert data.description is None
        assert data.estimated_effort is None
        assert data.reasoning is None

    def test_priority_must_be_positive(self):
        with pytest.raises(Exception):
            TaskSuggestionCreate(
                project_id=uuid.uuid4(),
                title="Bad priority",
                task_type="feature",
                priority=0,
            )


# ── Schema: TaskSuggestionUpdate ─────────────────────────────────────


class TestTaskSuggestionUpdateSchema:
    def test_partial_update(self):
        data = TaskSuggestionUpdate(title="Updated title")
        assert data.title == "Updated title"
        assert data.description is None
        assert data.priority is None

    def test_update_with_rejection_reason(self):
        data = TaskSuggestionUpdate(
            status=SchemaSuggestionStatus.rejected,
            rejection_reason="Not needed",
        )
        assert data.status == SchemaSuggestionStatus.rejected
        assert data.rejection_reason == "Not needed"


# ── Schema: TaskSuggestionResponse ───────────────────────────────────


class TestTaskSuggestionResponseSchema:
    def test_from_attributes(self):
        now = datetime.utcnow()
        pid = uuid.uuid4()
        sid = uuid.uuid4()
        data = TaskSuggestionResponse(
            id=sid,
            project_id=pid,
            title="Response test",
            description="desc",
            task_type="feature",
            priority=1,
            estimated_effort="1h",
            reasoning="because",
            status=SchemaSuggestionStatus.pending,
            rejection_reason=None,
            created_at=now,
            updated_at=now,
        )
        assert data.id == sid
        assert data.project_id == pid
        assert data.status == SchemaSuggestionStatus.pending

    def test_from_orm_model(self, db_session):
        """TaskSuggestionResponse should work with from_attributes=True."""
        # Simulate ORM-like object
        class FakeORM:
            id = uuid.uuid4()
            project_id = uuid.uuid4()
            title = "ORM test"
            description = "desc"
            task_type = "feature"
            priority = 1
            estimated_effort = "2h"
            reasoning = "reason"
            status = "pending"
            rejection_reason = None
            created_at = datetime.utcnow()
            updated_at = datetime.utcnow()

        resp = TaskSuggestionResponse.model_validate(FakeORM(), from_attributes=True)
        assert resp.title == "ORM test"
