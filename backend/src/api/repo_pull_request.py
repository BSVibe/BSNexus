"""G8.3 — ``POST /api/v1/requests/{id}/pr``.

Idempotent: opens (or returns the existing) GitHub PR from
``bsnexus/req-<id>`` against the project's base branch.

Surfaces ``PullRequestOpError.reason`` as a structured 4xx so the
frontend can render targeted copy:

  - repo_not_bound          → 412 (precondition failed)
  - missing_token           → 412
  - invalid_repo_url        → 422
  - base_branch_not_found   → 422
  - no_request_branch       → 412 (branch just created; no commits to PR)
  - empty_branch            → 412 (branch exists but matches base SHA)
  - github_auth             → 502
  - github_unavailable      → 502
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user, require_permission
from backend.src.core.git_ops import PullRequestOpError, open_request_pr
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Request
from backend.src.schemas.repo_pull_request import RequestPullRequestResponse
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/requests", tags=["repo-pull-request"])


_REASON_TO_STATUS: dict[str, int] = {
    "repo_not_bound": status.HTTP_412_PRECONDITION_FAILED,
    "missing_token": status.HTTP_412_PRECONDITION_FAILED,
    "no_request_branch": status.HTTP_412_PRECONDITION_FAILED,
    "empty_branch": status.HTTP_412_PRECONDITION_FAILED,
    "invalid_repo_url": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "base_branch_not_found": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "github_auth": status.HTTP_502_BAD_GATEWAY,
    "github_unavailable": status.HTTP_502_BAD_GATEWAY,
}


@router.post(
    "/{request_id}/pr",
    response_model=RequestPullRequestResponse,
    dependencies=[Depends(require_permission("bsnexus.repo_pull_request.write"))],
)
async def open_pr_for_request(
    request_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> RequestPullRequestResponse:
    stmt = select(Request).where(Request.id == request_id, Request.tenant_id == tenant_id)
    request_row = (await db.execute(stmt)).scalar_one_or_none()
    if request_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="request not found")

    try:
        info = await open_request_pr(request=request_row, session=db)
    except PullRequestOpError as exc:
        http_status = _REASON_TO_STATUS.get(exc.reason, status.HTTP_502_BAD_GATEWAY)
        logger.info(
            "open_request_pr_rejected",
            request_id=str(request_id),
            tenant_id=str(tenant_id),
            reason=exc.reason,
        )
        raise HTTPException(status_code=http_status, detail={"reason": exc.reason}) from exc

    await db.commit()
    return RequestPullRequestResponse(
        request_id=request_row.id,
        repo_url=info.repo_url,
        branch_name=info.branch_name,
        base_branch=info.base_branch,
        pr_number=info.pr_number,
        pr_url=info.pr_url,
        created=info.created,
    )
