"""PlannerService — generates daily task suggestions using LLM."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import litellm
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import SuggestionStatus, TaskSuggestion

logger = structlog.get_logger(__name__)

_REQUIRED_FIELDS = {"title", "task_type"}


class PlannerService:
    """Generates daily task suggestions via a single LLM call.

    Uses litellm.acompletion directly — no GatewayProvider indirection.
    Project context comes from the caller (planner API builds it from
    the existing plan tree + harness context).
    """

    def __init__(self, *, model: str, api_key: str, base_url: str | None = None) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url

    async def generate_daily_plan(
        self,
        project_id: str,
        db: AsyncSession,
        *,
        project_context: str = "",
    ) -> list[TaskSuggestion]:
        """Generate task suggestions for a project and persist them."""
        logger.info("planner_generate_start", project_id=project_id)

        messages = self._build_messages(project_id, project_context)

        response = await litellm.acompletion(
            model=self._model,
            messages=messages,
            api_key=self._api_key,
            api_base=self._base_url,
            temperature=0.3,
            max_tokens=4096,
            timeout=120,
        )
        content = response.choices[0].message.content or ""

        raw_suggestions = self._parse_llm_response(content)

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
        project_context: str,
    ) -> list[dict[str, str]]:
        system_prompt = (
            "You are a project planner for a software development team. "
            "Analyze the project context and suggest actionable tasks.\n\n"
            "Respond with a JSON array of task suggestions. Each suggestion must have:\n"
            '- "title": short task title (required)\n'
            '- "description": detailed description\n'
            '- "task_type": one of "feature", "bug", "improvement", "test", "chore", "refactor"\n'
            '- "priority": integer (1 = highest)\n'
            '- "estimated_effort": time estimate (e.g. "2h", "4h", "1d")\n'
            '- "reasoning": why this task matters\n\n'
            "Respond ONLY with the JSON array, no other text."
        )

        context_parts: list[str] = [f"Project ID: {project_id}"]
        if project_context:
            context_parts.append(project_context)

        user_message = (
            "Based on the following project context, generate task suggestions.\n\n"
            + "\n\n".join(context_parts)
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    @staticmethod
    def _parse_llm_response(content: str) -> list[dict[str, Any]]:
        text = content.strip()
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
