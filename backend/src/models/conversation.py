"""ConversationMessage model — persistent project chat history."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class ConversationMessage(Base):
    """One message in a project's Direction (single chat) thread.

    Source-agnostic: messages can originate from the web UI, Slack, or other
    integrations. The `source` and `external_*` fields let adapters round-trip.

    Each user message may generate or append to a Request. The `request_id`
    links the message to the Request it belongs to once extracted.
    """

    __tablename__ = "conversation_messages"
    __table_args__ = (
        Index(
            "ix_conversation_messages_project_created",
            "project_id",
            "created_at",
        ),
        Index("ix_conversation_messages_request", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)

    request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("requests.id", ondelete="SET NULL"),
        nullable=True,
    )

    actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")

    # Source channel for cross-platform (web/slack/etc.)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="web", server_default="web")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    thread_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
