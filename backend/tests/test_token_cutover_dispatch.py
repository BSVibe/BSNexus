"""Auth dispatch via ``bsvibe-authz``.

Pin: ``backend.src.core.auth.get_current_user`` dispatches by token prefix:

  * ``bsv_sk_*`` → :func:`bsvibe_authz.verify_opaque_token`
  * otherwise    → :func:`bsvibe_authz.verify_user_jwt`

Pre-existing guarantees that MUST hold:

  * ``E2E_TEST_TOKEN`` short-circuit still bypasses dispatch in non-prod.
  * Invalid arbitrary tokens return 401.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


@pytest_asyncio.fixture
async def authd_client(real_engine):
    """TestClient with real auth + tenant deps (only ``get_db`` overridden)."""
    sm = async_sessionmaker(real_engine, class_=AsyncSession, expire_on_commit=False)
    app = create_app(cors_origins=[], rate_limit=False)

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


@pytest.mark.asyncio
async def test_arbitrary_bsv_admin_token_returns_401(authd_client):
    """The legacy ``bsv_admin_*`` bootstrap path is gone — any such token
    is now an unrecognised garbage bearer and must be rejected with 401."""
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer bsv_admin_anything"},
        )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_invalid_token_returns_401(authd_client):
    """A garbage bearer that is neither ``bsv_sk_`` nor a valid JWT must
    return 401 — the JWT verifier rejects it via the bsvibe-authz
    dispatch path."""
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_e2e_bypass_short_circuits_before_dispatch(authd_client):
    """The pre-existing e2e bypass MUST keep working — it short-circuits
    BEFORE the bsvibe-authz dispatch so local Playwright runs are
    unaffected by the cutover."""
    bypass = "dev-bypass-token"
    e2e_tenant_id = "11111111-1111-4111-8111-111111111111"

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", bypass),
        patch("backend.src.core.auth.settings.e2e_test_user_id", "e2e-test-user"),
        patch("backend.src.core.auth.settings.e2e_test_user_email", "e2e@bsnexus.test"),
        patch("backend.src.core.auth.settings.e2e_test_user_tenant_id", e2e_tenant_id),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {bypass}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "e2e-test-user"
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_opaque_token_passes_when_introspection_returns_active(authd_client):
    """A ``bsv_sk_*`` opaque token must hit the introspection client and,
    on an active response, resolve to a User."""
    from unittest.mock import AsyncMock

    from bsvibe_authz.types import IntrospectionResponse

    fake_client = AsyncMock()
    fake_client.introspect = AsyncMock(
        return_value=IntrospectionResponse(
            active=True,
            sub="opaque-user-123",
            scope=["bsnexus.projects.read"],
        ),
    )

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth._get_introspection_client", return_value=fake_client),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer bsv_sk_some-opaque-token"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "opaque-user-123"
    fake_client.introspect.assert_awaited_once_with("bsv_sk_some-opaque-token")


@pytest.mark.asyncio
async def test_opaque_token_path_disabled_returns_401(authd_client):
    """When the introspection client is not configured the opaque path
    returns 401 — no fall-through to the JWT verifier."""
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.introspection_url", ""),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth._introspection_client", None),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer bsv_sk_no-introspection"},
        )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_query_string_token_works_for_sse(authd_client):
    """Browsers can't set Authorization headers on EventSource. The
    ``?token=`` query-string fallback must accept the same dispatch."""
    from unittest.mock import AsyncMock

    from bsvibe_authz.types import IntrospectionResponse

    fake_client = AsyncMock()
    fake_client.introspect = AsyncMock(
        return_value=IntrospectionResponse(
            active=True,
            sub="opaque-sse-user",
            scope=["bsnexus.events.read"],
        ),
    )

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth._get_introspection_client", return_value=fake_client),
    ):
        resp = await authd_client.get("/api/v1/auth/me?token=bsv_sk_query_string")

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_e2e_bypass_blocked_in_production_environment(authd_client):
    """The e2e bypass MUST stay disabled in production — a string equal
    to ``e2e_test_token`` but tried while ENVIRONMENT=production must
    NOT hit the bypass and must fall into the dispatch, which rejects
    it as an invalid JWT."""
    bypass = "leaked-dev-bypass"
    with (
        patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False),
        patch("backend.src.core.auth.settings.e2e_test_token", bypass),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {bypass}"},
        )
    assert resp.status_code == 401, resp.text
