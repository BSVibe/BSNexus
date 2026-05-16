"""G8.1 — ``POST /api/v1/requests/{id}/branch``.

Idempotent: creates ``bsnexus/req-<id>`` off the project's bound base
branch, or returns the existing ref. Surfaces ``BranchOpError.reason``
as a structured 4xx so the frontend can render targeted copy:

  - repo_not_bound          → 412 (precondition failed)
  - missing_token           → 412
  - invalid_repo_url        → 422
  - base_branch_not_found   → 422
  - github_auth             → 502 (bad gateway — upstream rejected our PAT)
  - github_unavailable      → 502
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user, require_permission
from backend.src.core.git_ops import BranchOpError, ensure_request_branch
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Request
from backend.src.schemas.repo_branch import RequestBranchResponse
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/requests", tags=["repo-branch"])


_REASON_TO_STATUS: dict[str, int] = {
    "repo_not_bound": status.HTTP_412_PRECONDITION_FAILED,
    "missing_token": status.HTTP_412_PRECONDITION_FAILED,
    "invalid_repo_url": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "base_branch_not_found": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "github_auth": status.HTTP_502_BAD_GATEWAY,
    "github_unavailable": status.HTTP_502_BAD_GATEWAY,
}


# NOTE: this is a mutating POST (ensures a git branch exists), but the
# Tier-5 permission matrix only defines ``repo_branch.read`` — there is
# no ``repo_branch.write`` row / OpenFGA relation. ``repo_branch`` is an
# admin-surface resource (``read`` already requires the ``admin`` role),
# so gating the ensure-branch POST on ``repo_branch.read`` still
# restricts it to admins, which is the intended floor. Using a
# non-matrix ``repo_branch.write`` string would map to an undefined
# OpenFGA relation. Reported as an ambiguity in the Tier 5 handoff.
@router.post(
    "/{request_id}/branch",
    response_model=RequestBranchResponse,
    dependencies=[Depends(require_permission("bsnexus.repo_branch.read"))],
)
async def ensure_branch_for_request(
    request_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> RequestBranchResponse:
    stmt = select(Request).where(Request.id == request_id, Request.tenant_id == tenant_id)
    request_row = (await db.execute(stmt)).scalar_one_or_none()
    if request_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="request not found")

    try:
        info = await ensure_request_branch(request=request_row, session=db)
    except BranchOpError as exc:
        http_status = _REASON_TO_STATUS.get(exc.reason, status.HTTP_502_BAD_GATEWAY)
        logger.info(
            "ensure_request_branch_rejected",
            request_id=str(request_id),
            tenant_id=str(tenant_id),
            reason=exc.reason,
        )
        raise HTTPException(status_code=http_status, detail={"reason": exc.reason}) from exc

    return RequestBranchResponse(
        request_id=request_row.id,
        repo_url=info.repo_url,
        base_branch=info.base_branch,
        branch_name=info.name,
        sha=info.sha,
        created=info.created,
    )
