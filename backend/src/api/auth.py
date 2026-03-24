"""Auth API endpoints — callback from BSVibe Auth portal + token management."""

from urllib.parse import urlencode

import structlog
from pydantic import BaseModel

import httpx
from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from backend.src.config import settings
from backend.src.core.auth import get_current_user

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["auth"])

_GOTRUE_TIMEOUT = 10.0


def _gotrue_url(path: str) -> str:
    return f"{settings.supabase_url}/auth/v1{path}"


def _anon_headers() -> dict[str, str]:
    return {
        "apikey": settings.supabase_anon_key,
        "Content-Type": "application/json",
    }


def _service_headers() -> dict[str, str]:
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }


# ── Schemas ──────────────────────────────────────────────────────────


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str | None = None
    role: str | None = None
    app_metadata: dict | None = None


# ── Callback (receives redirect from auth.bsvibe.dev) ───────────────


@router.get("/auth/callback")
async def auth_callback(
    access_token: str = Query(...),
    refresh_token: str = Query(...),
    state: str = Query(""),
) -> RedirectResponse:
    """Receive tokens from BSVibe Auth portal and redirect to frontend."""
    fragment = urlencode({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "state": state,
    })
    redirect_url = f"{settings.frontend_url}/auth/callback#{fragment}"
    return RedirectResponse(url=redirect_url, status_code=302)


# ── Token refresh ────────────────────────────────────────────────────


@router.post("/api/v1/auth/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest) -> TokenResponse:
    """Exchange a refresh token for a new access token."""
    async with httpx.AsyncClient(timeout=_GOTRUE_TIMEOUT) as client:
        resp = await client.post(
            _gotrue_url("/token?grant_type=refresh_token"),
            headers=_anon_headers(),
            json={"refresh_token": body.refresh_token},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    data = resp.json()
    return TokenResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_in=data["expires_in"],
    )


# ── Current user info ────────────────────────────────────────────────


@router.get("/api/v1/auth/me", response_model=UserResponse)
async def get_me(user: BSVibeUser = Depends(get_current_user)) -> UserResponse:
    """Return the current authenticated user's info."""
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.app_metadata.get("role"),
        app_metadata=user.app_metadata,
    )


# ── Logout ───────────────────────────────────────────────────────────


@router.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: BSVibeUser = Depends(get_current_user)) -> None:
    """Invalidate the user's session on Supabase (best-effort)."""
    if not settings.supabase_service_role_key:
        return

    try:
        async with httpx.AsyncClient(timeout=_GOTRUE_TIMEOUT) as client:
            await client.post(
                _gotrue_url(f"/admin/users/{user.id}/logout"),
                headers=_service_headers(),
            )
    except Exception:
        logger.warning("supabase_logout_failed", user_id=user.id, exc_info=True)
