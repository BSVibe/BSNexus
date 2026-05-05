"""BSNexus demo auth dependency.

In demo mode, replaces the prod ``get_current_user`` /
``get_tenant_id`` chain. The demo JWT (signed with DEMO_JWT_SECRET) is
verified, and ``request.state.tenant_id`` is stamped — preserving the
existing pattern in :mod:`backend.src.core.tenant_context`.
"""

from __future__ import annotations

import os
import uuid

from bsvibe_demo import DemoJWTError, decode_demo_jwt
from fastapi import HTTPException, Request, status

DEMO_COOKIE_NAME = "bsvibe_demo_session"


def get_demo_jwt_secret() -> str:
    secret = os.environ.get("DEMO_JWT_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DEMO_JWT_SECRET not configured on demo backend",
        )
    return secret


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get(DEMO_COOKIE_NAME)


async def demo_tenant_id(
    request: Request, *, secret: str | None = None
) -> uuid.UUID:
    """Return the verified tenant_id from the demo JWT.

    Drop-in replacement for ``backend.src.core.tenant_context.get_tenant_id``.
    Stamps ``request.state.tenant_id`` so existing handlers that read it
    continue to work.
    """
    secret = secret or get_demo_jwt_secret()
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Demo session not started — POST /api/v1/demo/session first",
        )

    try:
        claims = decode_demo_jwt(token, secret=secret)
    except DemoJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid demo session: {e}",
        ) from e

    request.state.tenant_id = claims.tenant_id
    return claims.tenant_id
