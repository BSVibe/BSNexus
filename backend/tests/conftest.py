"""Shared pytest fixtures.

Provides:
- ``db_engine`` — fresh in-memory SQLite engine with FK enforcement.
- ``test_session_maker`` — session factory bound to the test engine.
- ``db_session`` — one session per test.
- ``mock_stream_manager`` — AsyncMock standing in for Redis streams.
- ``mock_user`` — synthetic BSVibeUser (admin role).
- ``mock_tenant_id`` — stable UUID matching the mock user.
- ``seeded_tenant`` — persisted Tenant row matching ``mock_tenant_id``.
- ``test_app`` — fresh FastAPI instance (no rate limiting, no CORS).
- ``client`` — AsyncClient with db/user/stream deps overridden.
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("TESTING", "1")

from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import derive_personal_tenant_id, get_tenant_id
from backend.src.main import create_app
from backend.src.storage.database import Base, get_db

# Import models so Base.metadata.create_all picks them up.
import backend.src.models  # noqa: F401

TEST_DATABASE_URL = "sqlite+aiosqlite://"
TEST_USER_ID = "test-user-id"
TEST_TENANT_ID = derive_personal_tenant_id(TEST_USER_ID)


def _make_mock_user(role: str = "admin") -> MagicMock:
    """Synthetic BSVibeUser with a deterministic tenant id."""
    user = MagicMock()
    user.id = TEST_USER_ID
    user.email = "test@example.com"
    user.role = "authenticated"
    user.app_metadata = {"role": role, "tenant_id": str(TEST_TENANT_ID)}
    user.user_metadata = {}
    return user


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_connection, connection_record):
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
async def test_session_maker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(test_session_maker):
    async with test_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def mock_stream_manager():
    manager = AsyncMock()
    manager.publish = AsyncMock(return_value="mock-message-id")
    manager.publish_project_event = AsyncMock()
    manager.consume = AsyncMock(return_value=[])
    # SSE fan-out reads via ``tail`` (XREAD without consumer group). The
    # production primitive returns an empty list on block timeout; tests
    # mirror that so a busy-loop AsyncMock can't return Mock objects and
    # poison the generator.
    manager.tail = AsyncMock(return_value=[])
    manager.acknowledge = AsyncMock()
    manager.initialize_streams = AsyncMock()
    manager.redis = AsyncMock()
    manager.redis.get = AsyncMock(return_value=None)
    manager.redis.set = AsyncMock()
    manager.redis.incr = AsyncMock(return_value=1)
    manager.redis.expire = AsyncMock()
    return manager


@pytest_asyncio.fixture
async def mock_user():
    return _make_mock_user("admin")


@pytest_asyncio.fixture
async def mock_tenant_id() -> uuid.UUID:
    return TEST_TENANT_ID


@pytest_asyncio.fixture
async def seeded_tenant(db_session, mock_tenant_id):
    """Persist a Tenant row so FK-creating fixtures can reference it."""
    from backend.src.models import Tenant

    tenant = Tenant(
        id=mock_tenant_id,
        name="Test Tenant",
        slug=f"test-{mock_tenant_id.hex[:8]}",
        owner_user_id=TEST_USER_ID,
    )
    db_session.add(tenant)
    await db_session.commit()
    return tenant


@pytest_asyncio.fixture
async def test_app():
    return create_app(cors_origins=[], rate_limit=False)


@pytest_asyncio.fixture
async def client(test_app, db_session, mock_stream_manager, mock_user, mock_tenant_id, seeded_tenant):
    async def override_get_db():
        yield db_session

    async def override_get_current_user():
        return mock_user

    def override_get_tenant_id():
        return mock_tenant_id

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_current_user] = override_get_current_user
    test_app.dependency_overrides[get_tenant_id] = override_get_tenant_id
    test_app.state.stream_manager = mock_stream_manager
    test_app.state.rate_limit_disabled = True

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as test_client:
        yield test_client

    test_app.dependency_overrides.clear()
    for attr in ("stream_manager", "rate_limit_disabled"):
        if hasattr(test_app.state, attr):
            delattr(test_app.state, attr)
