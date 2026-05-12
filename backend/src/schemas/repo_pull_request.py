"""G8.3 — ``POST /api/v1/requests/{id}/pr`` response schema.

Idempotent open-pr endpoint. ``created`` distinguishes a freshly
opened PR from a re-fetch of an existing one for the same branch.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class RequestPullRequestResponse(BaseModel):
    request_id: uuid.UUID
    repo_url: str
    branch_name: str
    base_branch: str
    pr_number: int
    pr_url: str
    created: bool

    model_config = ConfigDict(extra="forbid")
