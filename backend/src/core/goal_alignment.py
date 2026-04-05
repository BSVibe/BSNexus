"""Goal alignment service — cascades mission context into task prompts."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Goal

logger = structlog.get_logger(__name__)

_MAX_ANCESTRY_DEPTH = 10


class GoalAlignmentService:
    """Resolves goal ancestry and injects context into prompts."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_goal_ancestry(self, goal_id: uuid.UUID) -> list[Goal]:
        """Walk up the goal hierarchy: task → project → department → mission."""
        chain: list[Goal] = []
        current_id: uuid.UUID | None = goal_id

        for _ in range(_MAX_ANCESTRY_DEPTH):
            if current_id is None:
                break
            result = await self.db.execute(select(Goal).where(Goal.id == current_id))
            goal = result.scalar_one_or_none()
            if goal is None:
                break
            chain.append(goal)
            current_id = goal.parent_goal_id

        chain.reverse()  # mission → ... → task
        return chain

    async def build_goal_context(self, goal_id: uuid.UUID | None) -> str:
        """Build a human-readable goal ancestry string for prompt injection."""
        if goal_id is None:
            return ""
        chain = await self.get_goal_ancestry(goal_id)
        if not chain:
            return ""

        lines = ["[Goal Alignment — why this task exists]"]
        for goal in chain:
            lines.append(f"  {goal.level.capitalize()}: {goal.title}")
        return "\n".join(lines)

    async def inject_goal_context(self, prompt: str, goal_id: uuid.UUID | None) -> str:
        """Prepend goal ancestry context to a task prompt."""
        context = await self.build_goal_context(goal_id)
        if not context:
            return prompt
        return f"{context}\n\n{prompt}"
