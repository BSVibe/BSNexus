"""TASK-004 — 3-way auth dispatch via ``bsvibe-authz``.

Pin: ``backend.src.core.auth.get_current_user`` no longer routes through
``bsvibe_auth.BsvibeAuthProvider``. It dispatches by token prefix:

  * ``bsv_admin_*`` → :func:`bsvibe_authz.verify_bootstrap_token`
  * ``bsv_sk_*``    → :func:`bsvibe_authz.verify_opaque_token`
  * otherwise       → :func:`bsvibe_authz.verify_user_jwt`

Pre-existing guarantees that MUST hold across the cutover:

  * ``E2E_TEST_TOKEN`` short-circuit still bypasses dispatch in non-prod.
  * Invalid bootstrap token (digest mismatch) returns 401.
  * Invalid arbitrary tokens return 401.
"""

from __future__ import annotations

import hashlib
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
async def test_bootstrap_token_grants_access_when_digest_matches(authd_client):
    """A ``bsv_admin_*`` token whose sha256 matches
    ``settings.bootstrap_token_hash`` MUST hit a protected endpoint and
    return 200 — the bootstrap path in the bsvibe-authz dispatch."""
    raw = "bsv_admin_secret-test-token-001"
    digest = hashlib.sha256(raw.encode()).hexdigest()

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", digest),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "bootstrap"
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_bootstrap_token_mismatch_returns_401(authd_client):
    """A ``bsv_admin_*`` token whose digest does NOT match must be
    rejected with 401 — and the response MUST NOT echo the raw token."""
    bogus_hash = "0" * 64

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", bogus_hash),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer bsv_admin_completely-wrong-secret"},
        )

    assert resp.status_code == 401, resp.text
    assert "bsv_admin_completely-wrong-secret" not in resp.text


@pytest.mark.asyncio
async def test_bootstrap_path_disabled_when_hash_empty(authd_client):
    """When ``bootstrap_token_hash`` is empty the bootstrap path MUST be
    disabled: any ``bsv_admin_*`` token is rejected, regardless of value."""
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer bsv_admin_anything"},
        )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_invalid_token_returns_401(authd_client):
    """A garbage bearer that is neither bsv_admin_/bsv_sk_ nor a valid
    JWT must return 401 — the JWT verifier rejects it via the
    bsvibe-authz dispatch path."""
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
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
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
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
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
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
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
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
    raw = "bsv_admin_query-bootstrap-token"
    digest = hashlib.sha256(raw.encode()).hexdigest()

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", digest),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        resp = await authd_client.get(f"/api/v1/auth/me?token={raw}")

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_bootstrap_token_blocked_in_production_environment(authd_client):
    """Even in production the bootstrap path is intentionally available
    (it's the recovery hatch). The e2e bypass, however, MUST stay
    disabled — pin the production guard against the e2e shortcut.

    Concretely: a string equal to ``e2e_test_token`` but tried while
    ENVIRONMENT=production must NOT hit the bypass and must instead
    fall into the dispatch, which rejects it as an invalid JWT."""
    bypass = "leaked-dev-bypass"
    with (
        patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", ""),
        patch("backend.src.core.auth.settings.e2e_test_token", bypass),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {bypass}"},
        )
    assert resp.status_code == 401, resp.text
