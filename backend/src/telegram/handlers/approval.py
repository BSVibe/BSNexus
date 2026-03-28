"""ApprovalHandler — handle inline keyboard callbacks for suggestion approve/reject."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from backend.src import models

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = structlog.get_logger(__name__)


class ApprovalHandler:
    """Handles approve/reject callbacks from briefing inline keyboards."""

    def __init__(self, db_session_factory: async_sessionmaker) -> None:
        self.db_session_factory = db_session_factory

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route callback query to approve or reject handler."""
        query = update.callback_query
        await query.answer()

        data = query.data or ""
        if ":" not in data:
            await query.edit_message_text("오류: 잘못된 요청입니다.")
            return

        action, raw_id = data.split(":", 1)

        try:
            suggestion_id = uuid.UUID(raw_id)
        except ValueError:
            await query.edit_message_text("오류: 잘못된 제안 ID입니다.")
            return

        if action == "approve":
            await self._handle_approve(query, suggestion_id)
        elif action == "reject":
            await self._handle_reject(query, context, suggestion_id)
        else:
            await query.edit_message_text("오류: 알 수 없는 작업입니다.")

    async def _handle_approve(self, query, suggestion_id: uuid.UUID) -> None:
        """Approve a suggestion: mark as approved and create a Task."""
        async with self.db_session_factory() as session:
            suggestion = await self._get_suggestion(session, suggestion_id)
            if suggestion is None:
                await query.edit_message_text("제안을 찾을 수 없습니다.")
                return

            if suggestion.status != models.SuggestionStatus.pending:
                await query.edit_message_text(
                    f"이미 처리된 제안입니다. (상태: {suggestion.status.value})"
                )
                return

            phase = await self._get_active_phase(session, suggestion.project_id)
            if phase is None:
                await query.edit_message_text("활성 페이즈가 없습니다. 프로젝트에 활성 페이즈를 먼저 생성해주세요.")
                return

            task = self._create_task_from_suggestion(suggestion, phase)
            session.add(task)
            suggestion.status = models.SuggestionStatus.approved
            await session.commit()

            logger.info("suggestion_approved_via_telegram", suggestion_id=str(suggestion_id))
            await query.edit_message_text(f"승인 완료: {suggestion.title}")

    async def _handle_reject(self, query, context: ContextTypes.DEFAULT_TYPE, suggestion_id: uuid.UUID) -> None:
        """Start rejection flow: ask for reason."""
        async with self.db_session_factory() as session:
            suggestion = await self._get_suggestion(session, suggestion_id)
            if suggestion is None:
                await query.edit_message_text("제안을 찾을 수 없습니다.")
                return

            if suggestion.status != models.SuggestionStatus.pending:
                await query.edit_message_text(
                    f"이미 처리된 제안입니다. (상태: {suggestion.status.value})"
                )
                return

        context.user_data["pending_rejection_id"] = str(suggestion_id)
        await query.edit_message_text(
            f"'{suggestion.title}' 제안의 거부 사유를 입력해주세요."
        )

    async def handle_rejection_reason(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Handle text message as rejection reason if a rejection is pending.

        Returns False if no rejection is pending (so the message can be handled elsewhere).
        """
        pending_id = context.user_data.get("pending_rejection_id")
        if not pending_id:
            return False

        reason = update.message.text
        suggestion_id = uuid.UUID(pending_id)

        async with self.db_session_factory() as session:
            suggestion = await self._get_suggestion(session, suggestion_id)
            if suggestion is None:
                del context.user_data["pending_rejection_id"]
                await update.message.reply_text("제안을 찾을 수 없습니다.")
                return True

            suggestion.status = models.SuggestionStatus.rejected
            suggestion.rejection_reason = reason
            await session.commit()

        del context.user_data["pending_rejection_id"]
        logger.info("suggestion_rejected_via_telegram", suggestion_id=str(suggestion_id), reason=reason)
        await update.message.reply_text(f"거부 완료: {suggestion.title}\n사유: {reason}")
        return True

    @staticmethod
    async def _get_suggestion(session, suggestion_id: uuid.UUID) -> models.TaskSuggestion | None:
        """Fetch a TaskSuggestion by ID."""
        result = await session.execute(
            select(models.TaskSuggestion).where(models.TaskSuggestion.id == suggestion_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_active_phase(session, project_id: uuid.UUID) -> models.Phase | None:
        """Fetch the first active phase for a project."""
        result = await session.execute(
            select(models.Phase).where(
                models.Phase.project_id == project_id,
                models.Phase.status == models.PhaseStatus.active,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _create_task_from_suggestion(
        suggestion: models.TaskSuggestion,
        phase: models.Phase,
    ) -> models.Task:
        """Convert a TaskSuggestion into a Task ORM object."""
        try:
            task_type = models.TaskType(suggestion.task_type)
        except ValueError:
            task_type = models.TaskType.feature

        if suggestion.priority <= 1:
            priority = models.TaskPriority.critical
        elif suggestion.priority <= 3:
            priority = models.TaskPriority.high
        elif suggestion.priority <= 6:
            priority = models.TaskPriority.medium
        else:
            priority = models.TaskPriority.low

        return models.Task(
            project_id=suggestion.project_id,
            phase_id=phase.id,
            title=suggestion.title,
            description=suggestion.description,
            task_type=task_type,
            priority=priority,
            source=models.TaskSource.architect,
            status=models.TaskStatus.ready,
            worker_prompt={"prompt": suggestion.description or suggestion.title},
            qa_prompt={"prompt": f"Verify: {suggestion.title}"},
        )
