"""TelegramBot — async wrapper around python-telegram-bot v20+ Application."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from backend.src.telegram.handlers.approval import ApprovalHandler

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = structlog.get_logger(__name__)


class TelegramBot:
    """Manages the Telegram bot lifecycle and command handlers.

    When ``token`` is empty the bot is disabled — start/stop become no-ops
    so the FastAPI app can run without Telegram credentials.
    """

    def __init__(self, token: str, chat_id: str, db_session_factory: async_sessionmaker | None = None) -> None:
        self.token = token
        self.chat_id = chat_id
        self.application = None
        self.approval_handler: ApprovalHandler | None = None

        if token:
            self.application = (
                ApplicationBuilder()
                .token(token)
                .build()
            )
            if db_session_factory is not None:
                self.approval_handler = ApprovalHandler(db_session_factory=db_session_factory)
            self._register_handlers()
            logger.info("telegram_bot_created", chat_id=chat_id)
        else:
            logger.info("telegram_bot_disabled", reason="no token")

    @property
    def is_enabled(self) -> bool:
        """Return True when the bot has a valid token and application."""
        return self.application is not None

    def _register_handlers(self) -> None:
        """Register all command handlers on the application."""
        self.application.add_handler(CommandHandler("start", self._handle_start))
        if self.approval_handler:
            self.application.add_handler(CallbackQueryHandler(self.approval_handler.handle_callback))
            self.application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
            )

    async def start(self) -> None:
        """Initialize and start polling for updates."""
        if not self.is_enabled:
            return
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        logger.info("telegram_bot_started")

    async def stop(self) -> None:
        """Gracefully shut down the bot."""
        if not self.is_enabled:
            return
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
        logger.info("telegram_bot_stopped")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle plain text messages — delegates to rejection reason handler if pending."""
        if self.approval_handler:
            await self.approval_handler.handle_rejection_reason(update, context)

    @staticmethod
    async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command — respond with Korean greeting."""
        await update.message.reply_text(
            "안녕하세요! BSNexus 텔레그램 봇입니다.\n"
            "매일 아침 작업 제안을 보내드리고, 승인/거부를 처리합니다.\n\n"
            "사용 가능한 명령어:\n"
            "/start - 봇 소개\n"
            "/status - 프로젝트 상태\n"
            "/plan - 오늘의 작업 제안 생성\n"
            "/cost - 비용 요약"
        )
