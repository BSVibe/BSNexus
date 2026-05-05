"""BSNexus demo HTTP endpoints.

Mounted at ``/api/v1/demo/*`` on the demo backend only (gated by
``BSVIBE_DEMO_MODE=true`` in main.py wiring). The prod backend never sees
these routes.
"""

from __future__ import annotations

from bsvibe_demo import DemoSessionResult, DemoSessionServiceSqlAlchemy
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from backend.src.demo.auth import (
    DEMO_COOKIE_NAME,
    demo_tenant_id,
    get_demo_jwt_secret,
)
from backend.src.demo.seed import seed_demo
from backend.src.storage.database import async_session

demo_router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class DemoSessionResponse(BaseModel):
    tenant_id: str
    token: str
    expires_in: int


def get_demo_session_service(
    secret: str = Depends(get_demo_jwt_secret),
) -> DemoSessionServiceSqlAlchemy:
    """Build a SQLAlchemy-backed DemoSessionService.

    BSNexus's ``tenants`` table requires ``owner_user_id`` NOT NULL —
    inject a synthetic value pinned to demo.
    """
    return DemoSessionServiceSqlAlchemy(
        sessionmaker=async_session,
        jwt_secret=secret,
        seed_fn=seed_demo,
        session_ttl_seconds=7200,
        tenant_extra_columns={"owner_user_id": "demo-visitor"},
    )


@demo_router.post(
    "/session",
    status_code=status.HTTP_201_CREATED,
    response_model=DemoSessionResponse,
    summary="Create a fresh demo session (ephemeral tenant + JWT)",
)
async def post_demo_session(
    response: Response,
    service: DemoSessionServiceSqlAlchemy = Depends(get_demo_session_service),
) -> DemoSessionResponse:
    """Create a new ephemeral demo tenant + seed it + return a JWT."""
    result: DemoSessionResult = await service.create_session()

    response.set_cookie(
        key=DEMO_COOKIE_NAME,
        value=result.token,
        max_age=result.expires_in,
        httponly=True,
        secure=True,
        samesite="lax",
    )

    return DemoSessionResponse(
        tenant_id=str(result.tenant_id),
        token=result.token,
        expires_in=result.expires_in,
    )


@demo_router.post(
    "/refresh",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Touch last_active so GC won't reap the tenant",
)
async def post_demo_refresh(
    request: Request,
    service: DemoSessionServiceSqlAlchemy = Depends(get_demo_session_service),
) -> Response:
    tid = await demo_tenant_id(request)
    await service.touch_last_active(tid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
