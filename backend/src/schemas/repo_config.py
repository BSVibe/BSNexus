"""Per-project repo binding (G8.0) schemas.

Reuses the existing ``projects.github_repo_url`` / ``github_branch`` /
``github_token_encrypted`` columns — there is no separate row.

Wire shape never exposes the raw token — only ``has_token``. The
``token`` field on PUT uses tri-state: omitted (preserve), null
(clear), non-empty string (replace + encrypt).
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RepoConfigResponse(BaseModel):
    project_id: uuid.UUID
    repo_url: str
    branch: str
    has_token: bool

    model_config = ConfigDict(extra="forbid")


class RepoConfigUpdate(BaseModel):
    repo_url: str = Field(..., min_length=1, max_length=500)
    branch: str = Field(..., min_length=1, max_length=255)
    token: str | None = Field(None, max_length=4_096)

    model_config = ConfigDict(extra="forbid")

    @field_validator("repo_url")
    @classmethod
    def _require_https(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.startswith("https://"):
            raise ValueError("repo_url must use https:// scheme")
        return stripped

    @field_validator("branch")
    @classmethod
    def _normalize_branch(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("branch must be a non-empty string")
        return stripped


def redacted(project: Any | None) -> RepoConfigResponse | None:
    """Wire-safe view of a Project's repo binding, or ``None`` when no
    repo is bound yet (``github_repo_url`` is unset).
    """
    if project is None or not project.github_repo_url:
        return None
    return RepoConfigResponse(
        project_id=project.id,
        repo_url=project.github_repo_url,
        branch=project.github_branch or "main",
        has_token=bool(project.github_token_encrypted),
    )
