"""Tests for PlannerService — direct litellm.acompletion, no provider indirection."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Project, TaskSuggestion, SuggestionStatus


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


def _make_llm_suggestions() -> list[dict]:
    return [
        {
            "title": "Add user authentication endpoint",
            "description": "Implement JWT-based auth",
            "task_type": "feature",
            "priority": 1,
            "estimated_effort": "4h",
            "reasoning": "Auth is required before any user-facing features",
        },
        {
            "title": "Fix database connection pool leak",
            "description": "Connections not being returned",
            "task_type": "bug",
            "priority": 2,
            "estimated_effort": "2h",
            "reasoning": "Production DB hitting max connections",
        },
    ]


def _mock_acompletion_response(suggestions: list[dict]) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=json.dumps(suggestions)))]
    return response


class TestPlannerServiceInit:
    def test_init_stores_config(self) -> None:
        from backend.src.core.planner_service import PlannerService
        svc = PlannerService(model="gpt-4o", api_key="sk-test")
        assert svc._model == "gpt-4o"
        assert svc._api_key == "sk-test"


class TestGenerateDailyPlan:
    @pytest.mark.asyncio
    @patch("backend.src.core.planner_service.litellm")
    async def test_creates_suggestions_from_llm_response(
        self, mock_litellm: MagicMock, db_session: AsyncSession, project: Project,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        suggestions = _make_llm_suggestions()
        mock_litellm.acompletion = AsyncMock(return_value=_mock_acompletion_response(suggestions))

        svc = PlannerService(model="test-model", api_key="test-key")
        result = await svc.generate_daily_plan(str(project.id), db_session)

        assert len(result) == 2
        assert all(isinstance(s, TaskSuggestion) for s in result)
        assert result[0].title == "Add user authentication endpoint"
        assert result[0].status == SuggestionStatus.pending

    @pytest.mark.asyncio
    @patch("backend.src.core.planner_service.litellm")
    async def test_persists_suggestions_to_db(
        self, mock_litellm: MagicMock, db_session: AsyncSession, project: Project,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        mock_litellm.acompletion = AsyncMock(
            return_value=_mock_acompletion_response(_make_llm_suggestions())
        )

        svc = PlannerService(model="test-model", api_key="test-key")
        await svc.generate_daily_plan(str(project.id), db_session)

        result = await db_session.execute(
            select(TaskSuggestion).where(TaskSuggestion.project_id == project.id)
        )
        saved = list(result.scalars().all())
        assert len(saved) == 2

    @pytest.mark.asyncio
    @patch("backend.src.core.planner_service.litellm")
    async def test_skips_invalid_suggestions(
        self, mock_litellm: MagicMock, db_session: AsyncSession, project: Project,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        # One valid, one missing required "title"
        suggestions = [
            {"title": "Valid task", "task_type": "feature"},
            {"description": "Missing title", "task_type": "bug"},
        ]
        mock_litellm.acompletion = AsyncMock(
            return_value=_mock_acompletion_response(suggestions)
        )

        svc = PlannerService(model="test-model", api_key="test-key")
        result = await svc.generate_daily_plan(str(project.id), db_session)
        assert len(result) == 1

    @pytest.mark.asyncio
    @patch("backend.src.core.planner_service.litellm")
    async def test_passes_project_context(
        self, mock_litellm: MagicMock, db_session: AsyncSession, project: Project,
    ) -> None:
        from backend.src.core.planner_service import PlannerService

        mock_litellm.acompletion = AsyncMock(
            return_value=_mock_acompletion_response([])
        )

        svc = PlannerService(model="test-model", api_key="test-key")
        await svc.generate_daily_plan(
            str(project.id), db_session,
            project_context="Current goal: build MVP",
        )

        call_args = mock_litellm.acompletion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Current goal: build MVP" in user_msg["content"]


class TestParseLLMResponse:
    def test_parses_json_array(self) -> None:
        from backend.src.core.planner_service import PlannerService
        result = PlannerService._parse_llm_response('[{"title": "test", "task_type": "feature"}]')
        assert len(result) == 1

    def test_parses_markdown_wrapped_json(self) -> None:
        from backend.src.core.planner_service import PlannerService
        result = PlannerService._parse_llm_response('```json\n[{"title": "test", "task_type": "feature"}]\n```')
        assert len(result) == 1

    def test_raises_on_invalid_json(self) -> None:
        from backend.src.core.planner_service import PlannerService
        with pytest.raises(ValueError, match="Failed to parse"):
            PlannerService._parse_llm_response("not json")

    def test_raises_on_non_array(self) -> None:
        from backend.src.core.planner_service import PlannerService
        with pytest.raises(ValueError, match="Expected JSON array"):
            PlannerService._parse_llm_response('{"title": "single object"}')
