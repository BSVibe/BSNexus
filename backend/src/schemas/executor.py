"""Per-tenant executor (LLM dispatch) config schemas.

Wire shape never exposes the raw api_key — only ``has_api_key``.
``api_key`` on PUT uses tri-state: omitted (preserve), null (clear),
non-empty string (replace + encrypt).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.src.models.executor_config import ExecutorKind


class ExecutorConfigResponse(BaseModel):
    kind: ExecutorKind
    enabled: bool
    base_url: str | None
    model: str | None
    has_api_key: bool
    extra_config: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class ExecutorConfigUpdate(BaseModel):
    kind: ExecutorKind
    enabled: bool = False
    base_url: str | None = Field(None, max_length=500)
    model: str | None = Field(None, max_length=255)
    api_key: str | None = Field(None, max_length=4_096)
    extra_config: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


def redacted(row: Any | None) -> ExecutorConfigResponse | None:
    if row is None:
        return None
    return ExecutorConfigResponse(
        kind=row.kind,
        enabled=row.enabled,
        base_url=row.base_url,
        model=row.model,
        has_api_key=bool(row.api_key_encrypted),
        extra_config=dict(row.extra_config or {}),
    )
