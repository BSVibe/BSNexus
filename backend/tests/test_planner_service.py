"""Tests for PlannerService — TDD: written before implementation."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Project, TaskSuggestion, SuggestionStatus
from backend.src.providers.gateway import ChatCompletionResult


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def project_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
async def project(db_session: AsyncSession, project_id: uuid.UUID) -> Project:
    p = Project(id=project_id, name="Test Project", description="A test project", repo_path="/tmp/test-repo")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest.fixture
def mock_gateway() -> AsyncMock:
    gateway = AsyncMock()
    return gateway


@pytest.fixture
def mock_knowledge() -> AsyncMock:
    knowledge = AsyncMock()
    knowledge.get_sot.return_value = {
        "project_id": "test",
        "content": "# Project SOT\nThis is a web app with React frontend and FastAPI backend.",
    }
    knowledge.search.return_value = [
        {"file": "sop.md", "content": "# SOP\nUse TDD for all features."},
    ]
    return knowledge


def _make_llm_suggestions() -> list[dict]:
    """Return a list of suggestion dicts as the LLM would produce."""
    return [
        {
            "title": "Add user authentication endpoint",
            "description": "Implement JWT-based auth with login/logout",
            "task_type": "feature",
            "priority": 1,
            "estimated_effort": "4h",
            "reasoning": "Auth is required before any user-facing features",
        },
        {
            "title": "Fix database connection pool leak",
            "description": "Connections not being returned after timeout",
            "task_type": "bugfix",
            "priority": 2,
            "estimated_effort": "2h",
            "reasoning": "Production DB hitting max connections during peak",
        },
    ]


@pytest.fixture
def mock_gateway_with_response(mock_gateway: AsyncMock) -> AsyncMock:
    """Gateway that returns a well-formed JSON response."""
    suggestions = _make_llm_suggestions()
    mock_gateway.chat_completion.return_value = ChatCompletionResult(
        content=json.dumps(suggestions),
        model="test-model",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )
    return mock_gateway


# ── PlannerService instantiation ─────────────────────────────────────


class TestPlannerServiceInit:
    def test_init_stores_providers(self, mock_gateway: AsyncMock, mock_knowledge: AsyncMock) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway, knowledge=mock_knowledge)
        assert svc._gateway is mock_gateway
        assert svc._knowledge is mock_knowledge

    def test_init_accepts_custom_model(self, mock_gateway: AsyncMock, mock_knowledge: AsyncMock) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway, knowledge=mock_knowledge, model_hint="gpt-4o")
        assert svc._model_hint == "gpt-4o"

    def test_init_default_model(self, mock_gateway: AsyncMock, mock_knowledge: AsyncMock) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway, knowledge=mock_knowledge)
        assert svc._model_hint is not None  # has a default


# ── generate_daily_plan ──────────────────────────────────────────────


class TestGenerateDailyPlan:
    async def test_calls_knowledge_provider_for_context(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        mock_knowledge.get_sot.assert_awaited_once_with(str(project.id))
        mock_knowledge.search.assert_awaited_once()

    async def test_calls_gateway_for_llm_completion(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        mock_gateway_with_response.chat_completion.assert_awaited_once()
        call_args = mock_gateway_with_response.chat_completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        assert len(messages) >= 1
        # Should include system message with context
        assert any(m["role"] == "system" for m in messages)

    async def test_returns_task_suggestions(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        result = await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        assert len(result) == 2
        assert result[0].title == "Add user authentication endpoint"
        assert result[1].title == "Fix database connection pool leak"

    async def test_suggestions_have_correct_fields(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        result = await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        s = result[0]
        assert s.project_id == project.id
        assert s.task_type == "feature"
        assert s.priority == 1
        assert s.estimated_effort == "4h"
        assert s.reasoning == "Auth is required before any user-facing features"
        assert s.status == SuggestionStatus.pending

    async def test_suggestions_persisted_to_db(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        stmt = select(TaskSuggestion).where(TaskSuggestion.project_id == project.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 2
        titles = {r.title for r in rows}
        assert "Add user authentication endpoint" in titles
        assert "Fix database connection pool leak" in titles

    async def test_suggestions_all_pending_status(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        stmt = select(TaskSuggestion).where(TaskSuggestion.project_id == project.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert all(r.status == SuggestionStatus.pending for r in rows)


# ── LLM response parsing edge cases ─────────────────────────────────


class TestParseLLMResponse:
    async def test_handles_json_wrapped_in_markdown(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        """LLMs often wrap JSON in ```json ... ``` blocks."""
        from backend.src.core.planner_service import PlannerService

        suggestions = _make_llm_suggestions()[:1]
        mock_gateway.chat_completion.return_value = ChatCompletionResult(
            content=f"```json\n{json.dumps(suggestions)}\n```",
            model="test-model",
        )

        svc = PlannerService(gateway=mock_gateway, knowledge=mock_knowledge)
        result = await svc.generate_daily_plan(project_id=str(project.id), db=db_session)
        assert len(result) == 1
        assert result[0].title == suggestions[0]["title"]

    async def test_handles_empty_suggestions_list(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        mock_gateway.chat_completion.return_value = ChatCompletionResult(
            content=json.dumps([]),
            model="test-model",
        )

        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway, knowledge=mock_knowledge)
        result = await svc.generate_daily_plan(project_id=str(project.id), db=db_session)
        assert result == []

    async def test_raises_on_invalid_json(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        mock_gateway.chat_completion.return_value = ChatCompletionResult(
            content="This is not JSON at all",
            model="test-model",
        )

        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway, knowledge=mock_knowledge)
        with pytest.raises(ValueError, match="parse"):
            await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

    async def test_skips_suggestions_missing_required_fields(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        """Suggestions missing title or task_type should be skipped, not crash."""
        suggestions = [
            {"title": "Valid task", "task_type": "feature", "priority": 1},
            {"description": "Missing title and task_type"},  # invalid
        ]
        mock_gateway.chat_completion.return_value = ChatCompletionResult(
            content=json.dumps(suggestions),
            model="test-model",
        )

        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway, knowledge=mock_knowledge)
        result = await svc.generate_daily_plan(project_id=str(project.id), db=db_session)
        assert len(result) == 1
        assert result[0].title == "Valid task"


# ── Prompt construction ──────────────────────────────────────────────


class TestPromptConstruction:
    async def test_prompt_includes_sot_context(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        call_args = mock_gateway_with_response.chat_completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        all_content = " ".join(m.get("content", "") for m in messages)
        assert "Project SOT" in all_content or "web app" in all_content

    async def test_prompt_includes_sop_context(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        call_args = mock_gateway_with_response.chat_completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        all_content = " ".join(m.get("content", "") for m in messages)
        assert "SOP" in all_content or "TDD" in all_content

    async def test_prompt_requests_json_array(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        call_args = mock_gateway_with_response.chat_completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        all_content = " ".join(m.get("content", "") for m in messages)
        assert "JSON" in all_content

    async def test_passes_model_hint_to_gateway(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        svc = PlannerService(
            gateway=mock_gateway_with_response,
            knowledge=mock_knowledge,
            model_hint="custom-model",
        )
        await svc.generate_daily_plan(project_id=str(project.id), db=db_session)

        call_args = mock_gateway_with_response.chat_completion.call_args
        model = call_args.kwargs.get("model_hint") or call_args[1].get("model_hint")
        assert model == "custom-model"

    async def test_knowledge_empty_sot_still_works(
        self,
        db_session: AsyncSession,
        project: Project,
        mock_gateway_with_response: AsyncMock,
        mock_knowledge: AsyncMock,
    ) -> None:
        """If knowledge provider returns empty SOT, plan generation should still work."""
        from backend.src.core.planner_service import PlannerService

        mock_knowledge.get_sot.return_value = {}
        mock_knowledge.search.return_value = []

        svc = PlannerService(gateway=mock_gateway_with_response, knowledge=mock_knowledge)
        result = await svc.generate_daily_plan(project_id=str(project.id), db=db_session)
        assert len(result) == 2  # still gets suggestions from LLM
