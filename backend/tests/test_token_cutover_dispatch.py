"""Auth dispatch via ``bsvibe-authz``.

Pin: ``backend.src.core.auth.get_current_user`` runs the bsvibe-authz
flow:

  * JWT-shaped token → :func:`bsvibe_authz.verify_user_jwt`. On
    failure, if the token still *looks* like a JWT and an
    introspection client is configured, fall through to
    :func:`bsvibe_authz.verify_via_introspection` (formerly
    ``verify_opaque_token``). This serves PAT JWTs from the device
    grant, which are signed with ``SERVICE_TOKEN_SIGNING_SECRET``
    rather than ``USER_JWT_SECRET``.
  * Non-JWT-shaped token → 401 without ever calling introspection.
    The legacy ``bsv_sk_*`` opaque token prefix dispatch was retired
    in bsvibe-authz 1.3.0 (Tier 2 of the 2026-05 auth cleanup).

Pre-existing guarantees that MUST hold:

  * ``E2E_TEST_TOKEN`` short-circuit still bypasses dispatch in non-prod.
  * Invalid arbitrary tokens return 401.
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.src.main import create_app
from backend.src.storage.database import Base, get_db

# Signing material used to forge a PAT-JWT-shaped token whose signature
# verifier (``verify_user_jwt`` with ``USER_JWT_SECRET``) fails — driving
# the introspection fallback in the new bsvibe-authz dispatch.
_PAT_SIGNING_SECRET = "dev-pat-signing-secret-with-at-least-32-bytes"
_USER_JWT_SECRET = "dev-user-jwt-secret-with-at-least-32-bytes-different"


def _build_pat_jwt(*, sub: str = "pat-user", scope: str = "bsnexus.projects.read") -> str:
    """Forge a JWT-shaped token that ``verify_user_jwt`` will reject.

    Signed with a secret that is NOT ``USER_JWT_SECRET``, so the lib's
    first verification step fails. Because the token still *looks* like
    a JWT (three base64url segments), the dispatch falls through to the
    introspection client — mirroring the device-grant PAT JWT flow.
    """
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
    """A garbage bearer that is neither JWT-shaped nor a valid JWT must
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
async def test_pat_jwt_passes_when_introspection_returns_active(authd_client):
    """A JWT-shaped token whose ``verify_user_jwt`` step fails (signed
    with the PAT signing secret rather than the user JWT secret) MUST
    fall through to the introspection client and, on an active
    response, resolve to a User.

    This is the post-1.3.0 surrogate for the retired ``bsv_sk_*``
    opaque path — JWT shape is now the dispatch gate.
    """
    from bsvibe_authz.types import IntrospectionResponse

    fake_client = AsyncMock()
    fake_client.introspect = AsyncMock(
        return_value=IntrospectionResponse(
            active=True,
            sub="pat-jwt-user",
            scope=["bsnexus.projects.read"],
        ),
    )

    pat_jwt = _build_pat_jwt(sub="pat-jwt-user")

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch.dict(os.environ, {"USER_JWT_SECRET": _USER_JWT_SECRET}),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth._get_introspection_client", return_value=fake_client),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {pat_jwt}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "pat-jwt-user"
    fake_client.introspect.assert_awaited_once_with(pat_jwt)


@pytest.mark.asyncio
async def test_legacy_bsv_sk_prefix_no_longer_dispatches(authd_client):
    """Regression guard for the 1.3.0 opaque retirement.

    A ``bsv_sk_*`` token is no longer JWT-shaped and is no longer
    recognised by any prefix branch — it must 401 WITHOUT triggering
    introspection. Before 1.3.0 this token shape was a dedicated
    dispatch branch; after, it is identical to any other garbage
    bearer.
    """
    fake_client = AsyncMock()
    fake_client.introspect = AsyncMock()

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

    assert resp.status_code == 401, resp.text
    fake_client.introspect.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_string_token_works_for_sse(authd_client):
    """Browsers can't set Authorization headers on EventSource. The
    ``?token=`` query-string fallback must accept the same dispatch."""
    from bsvibe_authz.types import IntrospectionResponse

    fake_client = AsyncMock()
    fake_client.introspect = AsyncMock(
        return_value=IntrospectionResponse(
            active=True,
            sub="pat-sse-user",
            scope=["bsnexus.events.read"],
        ),
    )

    pat_jwt = _build_pat_jwt(sub="pat-sse-user", scope="bsnexus.events.read")

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch.dict(os.environ, {"USER_JWT_SECRET": _USER_JWT_SECRET}),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth._get_introspection_client", return_value=fake_client),
    ):
        resp = await authd_client.get(f"/api/v1/auth/me?token={pat_jwt}")

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


@pytest.mark.asyncio
async def test_x_active_tenant_header_threaded_to_authz_dispatch(authd_client):
    """Tier 3.2: BSNexus re-wraps ``get_current_user`` and calls it
    directly, so it must thread the ``X-Active-Tenant`` request header —
    and the OpenFGA client the lib needs to membership-validate it —
    into the lib call. The raw Supabase JWT carries no tenant claim;
    without this the active tenant never resolves and every
    tenant-scoped route 403s."""
    from bsvibe_authz import User as AuthzUser

    captured: dict[str, object] = {}

    async def _fake_gcu(**kwargs: object) -> AuthzUser:
        captured.update(kwargs)
        return AuthzUser(id="u-1", email="u@bsvibe.dev", active_tenant_id="t-hdr")

    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch("backend.src.core.auth.bsvibe_authz_get_current_user", _fake_gcu),
    ):
        resp = await authd_client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer dummy.jwt.token",
                "X-Active-Tenant": "t-hdr",
            },
        )

    assert resp.status_code == 200, resp.text
    assert captured["x_active_tenant"] == "t-hdr"
    # The lib needs a real OpenFGA client to validate header-tenant
    # membership — a Depends() sentinel would AttributeError.
    assert captured["fga"] is not None
    assert hasattr(captured["fga"], "check")
