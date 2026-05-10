"""Workspace files schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class WorkspaceEntry(BaseModel):
    name: str
    kind: Literal["file", "dir"]
    size: int | None  # bytes for files, None for directories

    model_config = ConfigDict(extra="forbid")


class WorkspaceTreeResponse(BaseModel):
    path: str
    entries: list[WorkspaceEntry]

    model_config = ConfigDict(extra="forbid")


class WorkspaceContentResponse(BaseModel):
    path: str
    content: str
    size: int

    model_config = ConfigDict(extra="forbid")
