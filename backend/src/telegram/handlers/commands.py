"""CommandsHandler — /status, /plan, /cost command handlers."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from backend.src import models
from backend.src.telegram.handlers.briefing import BriefingHandler

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.src.core.planner_service import PlannerService

logger = structlog.get_logger(__name__)


class CommandsHandler:
    """Handles /status, /plan, and /cost Telegram commands."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        planner_service: PlannerService,
        chat_id: str,
        project_id: str,
    ) -> None:
        self.db_session_factory = db_session_factory
        self.planner_service = planner_service
        self.chat_id = chat_id
        self.project_id = project_id

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status — show project overview with active tasks and pending suggestions."""
        try:
            project_uuid = uuid.UUID(self.project_id)

            async with self.db_session_factory() as session:
                result_tasks = await session.execute(
                    select(func.count()).select_from(models.Task).where(
                        models.Task.project_id == project_uuid,
                        models.Task.status.in_([
                            models.TaskStatus.in_progress,
                            models.TaskStatus.review,
                            models.TaskStatus.ready,
                        ]),
                    )
                )
                active_count = result_tasks.scalar_one()

                result_suggestions = await session.execute(
                    select(func.count()).select_from(models.TaskSuggestion).where(
                        models.TaskSuggestion.project_id == project_uuid,
                        models.TaskSuggestion.status == models.SuggestionStatus.pending,
                    )
                )
                pending_count = result_suggestions.scalar_one()

            text = (
                "📊 프로젝트 상태\n\n"
                f"진행 중인 작업: {active_count}건\n"
                f"대기 중인 제안: {pending_count}건"
            )

            await update.message.reply_text(text)
            logger.info("status_command", project_id=self.project_id, active=active_count, pending=pending_count)
        except Exception:
            logger.error("status_command_error", project_id=self.project_id, exc_info=True)
            await update.message.reply_text("오류가 발생했습니다. 상태 조회에 실패했습니다.")

    async def handle_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /plan — trigger plan generation and send briefing."""
        await update.message.reply_text("작업 제안 생성 중...")

        try:
            async with self.db_session_factory() as session:
                suggestions = await self.planner_service.generate_daily_plan(self.project_id, session)

            text, keyboard_rows = BriefingHandler.format_briefing(suggestions)
            reply_markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None

            await update.message.reply_text(text, reply_markup=reply_markup)
            logger.info("plan_command", project_id=self.project_id, suggestion_count=len(suggestions))
        except Exception:
            logger.error("plan_command_error", project_id=self.project_id, exc_info=True)
            await update.message.reply_text("오류가 발생했습니다. 작업 제안 생성에 실패했습니다.")

    async def handle_cost(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cost — show cost summary (placeholder)."""
        text = (
            "💰 비용 요약\n\n"
            "비용 추적 서비스가 아직 연결되지 않았습니다.\n"
            "BSupervisor 연결 후 비용 데이터가 표시됩니다."
        )

        await update.message.reply_text(text)
        logger.info("cost_command", project_id=self.project_id)
