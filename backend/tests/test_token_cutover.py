"""Integration smoke for the bsvibe-authz token dispatch.

Single-file end-to-end exercise of the auth surface area:

  (a) PAT JWT (JWT-shaped, signed with the PAT signing secret rather
      than ``USER_JWT_SECRET``) via mocked introspection — viewer
      role, admin route returns 403 (PAT JWTs without an admin
      ``app_metadata.role`` cannot grant admin)
  (b) e2e bypass (``settings.e2e_test_token`` set, non-prod) keeps working
  (c) garbage bearer ⇒ 401

These overlap deliberately with ``test_token_cutover_dispatch.py``: that
file pins each dispatch branch in isolation; this file is the smoke test
that proves the wired path is live (route → ``get_current_user`` →
``_dispatch_token`` → ``BSVibeUser`` → ``require_role_permission``).

The legacy ``bsv_sk_*`` opaque token dispatch was retired in
bsvibe-authz 1.3.0 — JWT shape is now the dispatch gate.
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.src.core.auth import Permission, require_role_permission
from backend.src.core.tenant_context import BSVibeUser
from backend.src.main import create_app
from backend.src.storage.database import Base, get_db

# Signing material used to forge a PAT-JWT-shaped token whose
# ``verify_user_jwt`` step fails — driving the introspection fallback.
_PAT_SIGNING_SECRET = "dev-pat-signing-secret-with-at-least-32-bytes"
_USER_JWT_SECRET = "dev-user-jwt-secret-with-at-least-32-bytes-different"


def _build_pat_jwt(*, sub: str, scope: str = "bsnexus.projects.read") -> str:
    """Forge a JWT-shaped token that ``verify_user_jwt`` will reject so
    the dispatch falls through to introspection."""
    now = int(time.time())
    payload = {
        "iss": "https://auth.bsvibe.dev",
        "sub": sub,
        "aud": "bsnexus",
        "scope": scope,
        "iat": now,
        "exp": now + 3600,
        "token_type": "pat",
    }
    return pyjwt.encode(payload, _PAT_SIGNING_SECRET, algorithm="HS256")


@pytest_asyncio.fixture
async def real_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _build_admin_router() -> APIRouter:
    """Permission-gated test router (admin-only).

    Wired into the test app so we can prove that scope → role → RBAC
    actually flows end-to-end. ``Permission.admin_settings`` is on the
    admin role only; viewer must get 403.
    """
    router = APIRouter()

    @router.get("/api/v1/_test/admin-only")
    async def _admin_only(
        user: BSVibeUser = Depends(require_role_permission(Permission.admin_settings)),
    ) -> dict[str, str]:
        return {"id": user.id, "role": str(user.app_metadata.get("role"))}

    return router


@pytest_asyncio.fixture
async def smoke_client(real_engine) -> AsyncClient:
    """Smoke-test client: real auth deps + an admin-gated test route."""
    sm = async_sessionmaker(real_engine, class_=AsyncSession, expire_on_commit=False)
    app: FastAPI = create_app(cors_origins=[], rate_limit=False)
    app.include_router(_build_admin_router())

    async def override_db():
        async with sm() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.state.rate_limit_disabled = True

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.clear()


def _scrub_env() -> dict[str, str]:
    """Return os.environ minus ``ENVIRONMENT`` so tests are not impacted
    by a developer's local ``ENVIRONMENT=production`` shell export."""
    return {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}


# ── (a) PAT-JWT introspection-fallback path ──────────────────────────


@pytest.mark.asyncio
async def test_smoke_pat_jwt_viewer_returns_403(smoke_client):
    """PAT JWTs verified via introspection cannot grant admin (no
    ``app_metadata.role`` path). The smoke route is admin-gated → 403.

    Post-1.3.0 surrogate for the retired ``bsv_sk_*`` opaque smoke
    test — the bearer is a JWT-shaped token signed with the PAT
    secret (so ``verify_user_jwt`` fails) and the lib falls through
    to ``verify_via_introspection``.
    """
    from bsvibe_authz.types import IntrospectionResponse

    fake_client = AsyncMock()
    fake_client.introspect = AsyncMock(
        return_value=IntrospectionResponse(
            active=True,
            sub="pat-viewer",
            scope=["bsnexus.projects.read"],
        ),
    )

    pat_jwt = _build_pat_jwt(sub="pat-viewer")

    with (
        patch.dict(os.environ, _scrub_env(), clear=True),
        patch.dict(os.environ, {"USER_JWT_SECRET": _USER_JWT_SECRET}),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth._get_introspection_client", return_value=fake_client),
    ):
        resp = await smoke_client.get(
            "/api/v1/_test/admin-only",
            headers={"Authorization": f"Bearer {pat_jwt}"},
        )

    assert resp.status_code == 403, resp.text


# ── (b) E2E bypass regression guard ──────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_e2e_bypass_still_authenticates_in_dev(smoke_client):
    """The pre-existing ``e2e_test_token`` bypass MUST keep working in
    non-prod. Local Playwright runs depend on it — it short-circuits
    before the bsvibe-authz dispatch and must keep doing so."""
    bypass = "dev-e2e-bypass-token"
    e2e_tenant = "11111111-1111-4111-8111-111111111111"

    with (
        patch.dict(os.environ, _scrub_env(), clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", bypass),
        patch("backend.src.core.auth.settings.e2e_test_user_id", "e2e-test-user"),
        patch("backend.src.core.auth.settings.e2e_test_user_email", "e2e@bsnexus.test"),
        patch("backend.src.core.auth.settings.e2e_test_user_tenant_id", e2e_tenant),
    ):
        resp = await smoke_client.get(
            "/api/v1/_test/admin-only",
            headers={"Authorization": f"Bearer {bypass}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "e2e-test-user"
    assert body["role"] == "admin"


# ── (c) Invalid token ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_invalid_token_returns_401(smoke_client):
    """Garbage bearer that isn't a valid JWT must produce a clean
    401 — and the response MUST NOT echo the raw token."""
    bogus = "not-a-token-at-all"
    with (
        patch.dict(os.environ, _scrub_env(), clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await smoke_client.get(
            "/api/v1/_test/admin-only",
            headers={"Authorization": f"Bearer {bogus}"},
        )

    assert resp.status_code == 401, resp.text
    assert bogus not in resp.text
    assert resp.headers.get("www-authenticate", "").lower().startswith("bearer")
