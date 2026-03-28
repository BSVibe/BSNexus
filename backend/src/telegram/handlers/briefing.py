"""BriefingHandler — morning briefing with daily task suggestions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.src.core.planner_service import PlannerService
    from backend.src.models import TaskSuggestion

logger = structlog.get_logger(__name__)


class BriefingHandler:
    """Generates and sends morning briefing messages with task suggestions."""

    def __init__(
        self,
        planner_service: PlannerService,
        db_session_factory: async_sessionmaker,
        chat_id: str,
        project_id: str,
    ) -> None:
        self.planner_service = planner_service
        self.db_session_factory = db_session_factory
        self.chat_id = chat_id
        self.project_id = project_id

    @staticmethod
    def format_briefing(
        suggestions: list[TaskSuggestion],
    ) -> tuple[str, list[list[InlineKeyboardButton]]]:
        """Format suggestions into a Korean message with inline keyboard buttons.

        Returns (message_text, keyboard_rows).
        """
        if not suggestions:
            return "제안된 작업이 없습니다.", []

        lines = ["오늘의 작업 제안\n"]
        keyboard: list[list[InlineKeyboardButton]] = []

        for i, s in enumerate(suggestions, 1):
            lines.append(
                f"{i}. {s.title}\n"
                f"   유형: {s.task_type} | 우선순위: {s.priority}"
                f"{f' | 예상: {s.estimated_effort}' if s.estimated_effort else ''}\n"
                f"   {s.reasoning or ''}"
            )
            keyboard.append([
                InlineKeyboardButton("승인", callback_data=f"approve:{s.id}"),
                InlineKeyboardButton("거부", callback_data=f"reject:{s.id}"),
            ])

        return "\n".join(lines), keyboard

    async def send_briefing(self, bot: Bot) -> None:
        """Generate daily plan and send briefing message to the configured chat."""
        logger.info("briefing_start", project_id=self.project_id, chat_id=self.chat_id)

        async with self.db_session_factory() as session:
            suggestions = await self.planner_service.generate_daily_plan(
                self.project_id, session
            )

        text, keyboard_rows = self.format_briefing(suggestions)

        reply_markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None
        await bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=reply_markup,
        )

        logger.info("briefing_sent", suggestion_count=len(suggestions))
