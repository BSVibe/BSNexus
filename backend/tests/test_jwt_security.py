"""S0-3 — JWT signature verification + tenant-spoofing regression tests.

These tests exercise the *real* auth + tenant-context wiring (no
``dependency_overrides`` of ``get_current_user`` / ``get_tenant_id``)
and assert the C3 vulnerability is closed:

1. A bearer token whose JWT signature is invalid (i.e. ``verify_token``
   raises ``AuthError``) MUST be rejected with 401, even when the
   payload claims a valid-looking ``tenant_id`` — the unverified payload
   in ``TenantMiddleware`` must not let the request reach a handler.

2. A bearer token that *is* signed by bsvibe-auth but carries a
   ``tenant_id`` claim pointing at a different tenant MUST NOT be
   allowed to read or write rows belonging to that other tenant.
   Concretely: rows seeded under tenant B are not returned to a request
   whose verified user belongs to tenant A.

3. ``E2E_TEST_TOKEN`` MUST be ignored when ``ENVIRONMENT=production``,
   so a leaked dev bypass cannot grant admin in prod.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from bsvibe_auth import BSVibeUser
from bsvibe_auth.errors import TokenInvalidError
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.src.core.tenant_context import derive_personal_tenant_id
from backend.src.main import create_app
from backend.src.storage.database import Base, get_db


def _encode_jwt_like(payload: dict) -> str:
    """Build a JWT-shaped token with arbitrary payload (signature is junk)."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.not-a-real-signature"


@pytest_asyncio.fixture
async def real_auth_engine():
    """Fresh in-memory SQLite engine for the security regression tests."""
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
async def real_auth_session_maker(real_auth_engine):
    return async_sessionmaker(real_auth_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def real_auth_client(real_auth_session_maker):
    """Client that exercises the real auth + tenant deps.

    Only ``get_db`` is overridden (so we don't need a live PG).
    ``get_current_user`` and ``get_tenant_id`` are *not* overridden —
    every request goes through ``TenantMiddleware`` and
    ``BsvibeAuthProvider.verify_token`` for real, with the latter
    patched per-test.
    """
    app = create_app(cors_origins=[], rate_limit=False)

    async def override_get_db():
        async with real_auth_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.state.rate_limit_disabled = True

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as test_client:
        yield test_client

    app.dependency_overrides.clear()


# ── 1. Forged signature ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forged_jwt_signature_returns_401(real_auth_client):
    """A token that fails ``verify_token`` must hit 401 even if the
    payload contains a tenant_id claim — proves the unverified payload
    in ``TenantMiddleware`` cannot be used to short-circuit auth."""
    forged_tid = uuid.uuid4()
    token = _encode_jwt_like(
        {
            "sub": "attacker",
            "email": "evil@example.com",
            "app_metadata": {"tenant_id": str(forged_tid), "role": "admin"},
        }
    )

    with (
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch(
            "backend.src.core.auth.auth_provider.verify_token",
            new=AsyncMock(side_effect=TokenInvalidError("bad signature")),
        ),
    ):
        resp = await real_auth_client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_missing_bearer_token_returns_401(real_auth_client):
    resp = await real_auth_client.get("/api/v1/projects")
    assert resp.status_code == 401, resp.text


# ── 2. Tenant spoofing ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spoofed_tenant_claim_does_not_leak_other_tenant_rows(real_auth_client, real_auth_session_maker):
    """JWT carries ``app_metadata.tenant_id`` of an *other* tenant the
    user does not own. The handler must not return that other tenant's
    rows — the verified user controls only their personal tenant.
    """
    from backend.src.models import Project, Tenant

    victim_user_id = "victim-user"
    victim_tenant_id = derive_personal_tenant_id(victim_user_id)
    attacker_user_id = "attacker-user"
    attacker_tenant_id = derive_personal_tenant_id(attacker_user_id)
    assert victim_tenant_id != attacker_tenant_id

    async with real_auth_session_maker() as session:
        session.add_all(
            [
                Tenant(
                    id=victim_tenant_id,
                    name="victim",
                    slug=f"v-{victim_tenant_id.hex[:8]}",
                    owner_user_id=victim_user_id,
                ),
                Tenant(
                    id=attacker_tenant_id,
                    name="attacker",
                    slug=f"a-{attacker_tenant_id.hex[:8]}",
                    owner_user_id=attacker_user_id,
                ),
            ]
        )
        await session.commit()
        secret_project = Project(tenant_id=victim_tenant_id, name="VictimSecret", description="confidential")
        session.add(secret_project)
        await session.commit()

    # The attacker's JWT *claims* victim_tenant_id but is verified as
    # attacker user. Spoofing must be ignored.
    spoofed_token = _encode_jwt_like(
        {
            "sub": attacker_user_id,
            "email": "attacker@example.com",
            "app_metadata": {
                "tenant_id": str(victim_tenant_id),
                "role": "admin",
            },
        }
    )

    verified_attacker = BSVibeUser(
        id=attacker_user_id,
        email="attacker@example.com",
        # The verified principal has its OWN tenant; auth-server-issued
        # claims also bind to attacker's real id, never the spoofed one.
        app_metadata={"role": "admin"},
        user_metadata={},
    )

    with (
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch(
            "backend.src.core.auth.auth_provider.verify_token",
            new=AsyncMock(return_value=verified_attacker),
        ),
    ):
        resp = await real_auth_client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {spoofed_token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [row["name"] for row in body]
    assert "VictimSecret" not in names, f"Tenant spoofing leaked victim's project. Returned rows: {body}"
    # Every returned row must be scoped to the *attacker's own* tenant.
    for row in body:
        assert row["tenant_id"] == str(attacker_tenant_id), f"Row {row} is not in attacker's tenant"


@pytest.mark.asyncio
async def test_spoofed_tenant_claim_cannot_create_row_under_other_tenant(real_auth_client, real_auth_session_maker):
    """POST /projects with spoofed tenant_id must persist the row under
    the *verified* user's tenant, not the spoofed one."""
    from backend.src.models import Project

    attacker_user_id = "attacker-create-user"
    attacker_tenant_id = derive_personal_tenant_id(attacker_user_id)
    spoofed_tenant_id = uuid.uuid4()
    assert spoofed_tenant_id != attacker_tenant_id

    spoofed_token = _encode_jwt_like(
        {
            "sub": attacker_user_id,
            "email": "attacker2@example.com",
            "app_metadata": {
                "tenant_id": str(spoofed_tenant_id),
                "role": "admin",
            },
        }
    )

    verified_attacker = BSVibeUser(
        id=attacker_user_id,
        email="attacker2@example.com",
        app_metadata={"role": "admin"},
        user_metadata={},
    )

    with (
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
        patch(
            "backend.src.core.auth.auth_provider.verify_token",
            new=AsyncMock(return_value=verified_attacker),
        ),
    ):
        resp = await real_auth_client.post(
            "/api/v1/projects",
            json={"name": "Pwn"},
            headers={"Authorization": f"Bearer {spoofed_token}"},
        )

    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["tenant_id"] == str(attacker_tenant_id)
    assert created["tenant_id"] != str(spoofed_tenant_id)

    async with real_auth_session_maker() as session:
        from sqlalchemy import select

        rows = (await session.execute(select(Project).where(Project.name == "Pwn"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].tenant_id == attacker_tenant_id


# ── 3. E2E_TEST_TOKEN production guard ──────────────────────────────


@pytest.mark.asyncio
async def test_e2e_test_token_blocked_in_production_environment(real_auth_client):
    """When ``ENVIRONMENT=production`` the e2e bypass token must be
    rejected — even if it's somehow set in the env, it cannot be used
    to short-circuit auth."""
    bypass = "leaked-dev-bypass"

    with (
        patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False),
        patch("backend.src.core.auth.settings.e2e_test_token", bypass),
        patch("backend.src.core.auth.settings.e2e_test_user_id", "e2e-test-user"),
        patch("backend.src.core.auth.settings.e2e_test_user_email", "e2e@bsnexus.test"),
        patch(
            "backend.src.core.auth.settings.e2e_test_user_tenant_id",
            "11111111-1111-4111-8111-111111111111",
        ),
        patch(
            "backend.src.core.auth.auth_provider.verify_token",
            new=AsyncMock(side_effect=TokenInvalidError("not a real jwt")),
        ),
    ):
        resp = await real_auth_client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {bypass}"},
        )

    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_e2e_test_token_works_in_non_production(real_auth_client):
    """Sanity check that the bypass still works when ENVIRONMENT is unset
    or non-production — otherwise local dev / CI breaks."""
    bypass = "dev-bypass-token"
    e2e_tenant_id = "11111111-1111-4111-8111-111111111111"

    env_without_environment = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env_without_environment, clear=True),
        patch("backend.src.core.auth.settings.e2e_test_token", bypass),
        patch("backend.src.core.auth.settings.e2e_test_user_id", "e2e-test-user"),
        patch("backend.src.core.auth.settings.e2e_test_user_email", "e2e@bsnexus.test"),
        patch("backend.src.core.auth.settings.e2e_test_user_tenant_id", e2e_tenant_id),
    ):
        resp = await real_auth_client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {bypass}"},
        )

    assert resp.status_code == 200, resp.text
