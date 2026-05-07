"""TASK-005 — Integration smoke for the bsvibe-authz 3-way cutover.

Single-file end-to-end exercise of the token-cutover surface area:

  (a) bootstrap admin token (``bsv_admin_*``) hits a protected route → 200
  (b) opaque token (``bsv_sk_*``) via mocked introspection
        - ``*`` scope ⇒ admin ⇒ permission-gated route returns 200
        - narrow scope ⇒ viewer ⇒ same route returns 403
  (c) e2e bypass (``settings.e2e_test_token`` set, non-prod) keeps working
  (d) garbage bearer ⇒ 401

These overlap deliberately with ``test_token_cutover_dispatch.py``: that
file pins each dispatch branch in isolation; this file is the smoke test
that proves the wired path is live (route → ``get_current_user`` →
``_dispatch_token`` → ``BSVibeUser`` → ``require_permission``).
"""

from __future__ import annotations

import hashlib
import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.src.core.auth import Permission, require_permission
from backend.src.core.tenant_context import BSVibeUser
from backend.src.main import create_app
from backend.src.storage.database import Base, get_db


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
        user: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
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


# ── (a) Bootstrap path ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_bootstrap_admin_hits_protected_route(smoke_client):
    raw = "bsv_admin_smoke-bootstrap-001"
    digest = hashlib.sha256(raw.encode()).hexdigest()

    with (
        patch.dict(os.environ, _scrub_env(), clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", digest),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await smoke_client.get(
            "/api/v1/_test/admin-only",
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "admin"


# ── (b) Opaque path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_opaque_admin_scope_grants_access(smoke_client):
    """Opaque token with ``*`` scope ⇒ admin ⇒ permission route 200."""
    from bsvibe_authz.types import IntrospectionResponse

    fake_client = AsyncMock()
    fake_client.introspect = AsyncMock(
        return_value=IntrospectionResponse(
            active=True,
            sub="opaque-admin",
            scope=["*"],
        ),
    )

    with (
        patch.dict(os.environ, _scrub_env(), clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth._get_introspection_client", return_value=fake_client),
    ):
        resp = await smoke_client.get(
            "/api/v1/_test/admin-only",
            headers={"Authorization": "Bearer bsv_sk_admin-scope-token"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "opaque-admin"
    fake_client.introspect.assert_awaited_once()


@pytest.mark.asyncio
async def test_smoke_opaque_narrow_scope_returns_403(smoke_client):
    """Opaque token without ``*`` scope ⇒ viewer ⇒ admin route 403."""
    from bsvibe_authz.types import IntrospectionResponse

    fake_client = AsyncMock()
    fake_client.introspect = AsyncMock(
        return_value=IntrospectionResponse(
            active=True,
            sub="opaque-viewer",
            scope=["bsnexus.projects.read"],
        ),
    )

    with (
        patch.dict(os.environ, _scrub_env(), clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth._get_introspection_client", return_value=fake_client),
    ):
        resp = await smoke_client.get(
            "/api/v1/_test/admin-only",
            headers={"Authorization": "Bearer bsv_sk_narrow-scope"},
        )

    assert resp.status_code == 403, resp.text


# ── (c) E2E bypass regression guard ──────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_e2e_bypass_still_authenticates_in_dev(smoke_client):
    """The pre-existing ``e2e_test_token`` bypass MUST keep working in
    non-prod. Local Playwright runs depend on it — it short-circuits
    before the bsvibe-authz dispatch and must keep doing so."""
    bypass = "dev-e2e-bypass-token"
    e2e_tenant = "11111111-1111-4111-8111-111111111111"

    with (
        patch.dict(os.environ, _scrub_env(), clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
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


# ── (d) Invalid token ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_invalid_token_returns_401(smoke_client):
    """Garbage bearer that's neither bootstrap, opaque, nor a JWT must
    produce a clean 401 — and the response MUST NOT echo the raw token."""
    bogus = "not-a-token-at-all"
    with (
        patch.dict(os.environ, _scrub_env(), clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await smoke_client.get(
            "/api/v1/_test/admin-only",
            headers={"Authorization": f"Bearer {bogus}"},
        )

    assert resp.status_code == 401, resp.text
    assert bogus not in resp.text
    assert resp.headers.get("www-authenticate", "").lower().startswith("bearer")
