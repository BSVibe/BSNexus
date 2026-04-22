"""Conversation message schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=50_000)

    model_config = ConfigDict(extra="forbid")


class MessageResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    role: str  # "user" | "assistant"
    content: str
    request_id: uuid.UUID | None
    actions: list
    source: str
    external_id: str | None
    thread_ref: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SendMessageResponse(BaseModel):
    """Returned after a user message is classified + extracted."""

    message: MessageResponse
    intent: str  # "chit_chat" | "question" | "request" | "modification"
    request_id: uuid.UUID | None
    request_created: bool
    intent_summary: str | None = None
