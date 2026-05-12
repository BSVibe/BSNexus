"""G8.1 — ``POST /api/v1/requests/{id}/branch`` response schema.

Idempotent ensure-branch endpoint. ``created`` distinguishes a fresh
ref from a re-fetch of an existing one.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class RequestBranchResponse(BaseModel):
    request_id: uuid.UUID
    repo_url: str
    base_branch: str
    branch_name: str
    sha: str
    created: bool

    model_config = ConfigDict(extra="forbid")
