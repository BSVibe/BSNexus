"""PlannerService — generates daily task suggestions using LLM and knowledge context."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings
from backend.src.models import SuggestionStatus, TaskSuggestion
from backend.src.providers.gateway import GatewayProvider
from backend.src.providers.knowledge import KnowledgeProvider

logger = structlog.get_logger(__name__)

_REQUIRED_FIELDS = {"title", "task_type"}


class PlannerService:
    """Generates daily task suggestions by combining project knowledge with LLM planning."""

    def __init__(
        self,
        gateway: GatewayProvider,
        knowledge: KnowledgeProvider,
        model_hint: str | None = None,
    ) -> None:
        self._gateway = gateway
        self._knowledge = knowledge
        self._model_hint = model_hint or settings.default_llm_model

    async def generate_daily_plan(
        self,
        project_id: str,
        db: AsyncSession,
    ) -> list[TaskSuggestion]:
        """Generate task suggestions for a project and persist them.

        1. Fetches SOT and SOP context from KnowledgeProvider
        2. Builds a structured prompt for the LLM
        3. Calls GatewayProvider for completion
        4. Parses JSON response into TaskSuggestion models
        5. Persists suggestions to the database
        """
        logger.info("planner_generate_start", project_id=project_id)

        # 1. Gather context
        sot = await self._knowledge.get_sot(project_id)
        sop_results = await self._knowledge.search("SOP standard operating procedures", limit=5)

        # 2. Build prompt
        messages = self._build_messages(project_id, sot, sop_results)

        # 3. Call LLM
        result = await self._gateway.chat_completion(
            messages=messages,
            model_hint=self._model_hint,
            task_metadata={"project_id": project_id, "action": "daily_plan"},
        )

        # 4. Parse response
        raw_suggestions = self._parse_llm_response(result.content)

        # 5. Build and persist models
        project_uuid = uuid.UUID(project_id)
        suggestions: list[TaskSuggestion] = []

        for item in raw_suggestions:
            if not _REQUIRED_FIELDS.issubset(item.keys()):
                logger.warning("planner_skip_invalid_suggestion", missing=list(_REQUIRED_FIELDS - item.keys()))
                continue

            suggestion = TaskSuggestion(
                project_id=project_uuid,
                title=item["title"],
                description=item.get("description"),
                task_type=item["task_type"],
                priority=item.get("priority", 99),
                estimated_effort=item.get("estimated_effort"),
                reasoning=item.get("reasoning"),
                status=SuggestionStatus.pending,
            )
            db.add(suggestion)
            suggestions.append(suggestion)

        await db.commit()
        for s in suggestions:
            await db.refresh(s)

        logger.info("planner_generate_done", project_id=project_id, suggestion_count=len(suggestions))
        return suggestions

    def _build_messages(
        self,
        project_id: str,
        sot: dict[str, Any],
        sop_results: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Build LLM messages with project context."""
        sot_text = sot.get("content", "") if sot else ""
        sop_text = "\n\n".join(r.get("content", "") for r in sop_results) if sop_results else ""

        system_prompt = (
            "You are a project planner for a software development team. "
            "Analyze the project context and suggest actionable tasks for today.\n\n"
            "Respond with a JSON array of task suggestions. Each suggestion must have:\n"
            '- "title": short task title (required)\n'
            '- "description": detailed description\n'
            '- "task_type": one of "feature", "bugfix", "refactor", "test", "docs"\n'
            '- "priority": integer (1 = highest)\n'
            '- "estimated_effort": time estimate (e.g. "2h", "4h", "1d")\n'
            '- "reasoning": why this task matters today\n\n'
            "Respond ONLY with the JSON array, no other text."
        )

        context_parts: list[str] = [f"Project ID: {project_id}"]
        if sot_text:
            context_parts.append(f"## Project SOT (Source of Truth)\n{sot_text}")
        if sop_text:
            context_parts.append(f"## SOP (Standard Operating Procedures)\n{sop_text}")

        user_message = (
            "Based on the following project context, generate a daily plan with task suggestions.\n\n"
            + "\n\n".join(context_parts)
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    @staticmethod
    def _parse_llm_response(content: str) -> list[dict[str, Any]]:
        """Parse LLM response content into a list of suggestion dicts.

        Handles JSON wrapped in markdown code blocks.
        """
        text = content.strip()

        # Strip markdown code fences if present
        md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if md_match:
            text = md_match.group(1).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse LLM response as JSON: {exc}") from exc

        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")

        return parsed
