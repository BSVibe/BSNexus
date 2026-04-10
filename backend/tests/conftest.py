from __future__ import annotations

import os

os.environ.setdefault("TESTING", "1")

from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.src.main import create_app
from backend.src.core.auth import get_current_user
from backend.src.storage.database import Base, get_db

# Import models so Base.metadata.create_all picks them up.
# Security models (audit_logger, compliance) are loaded
# transitively via main.py -> security router imports.
import backend.src.models  # noqa: F401

TEST_DATABASE_URL = "sqlite+aiosqlite://"


def _make_mock_user(role: str = "admin") -> MagicMock:
    """Create a mock BSVibeUser with the given role."""
    user = MagicMock()
    user.id = "test-user-id"
    user.email = "test@example.com"
    user.role = "authenticated"
    user.app_metadata = {"role": role}
    user.user_metadata = {}
    return user


@pytest_asyncio.fixture
async def db_engine():
    """Create a test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # Enable foreign key support for SQLite
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
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
    """Create a session maker bound to the test database engine."""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(test_session_maker):
    """Create a test database session."""
    async with test_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def mock_stream_manager():
    """Create a mock RedisStreamManager."""
    manager = AsyncMock()
    manager.publish = AsyncMock(return_value="mock-message-id")
    manager.publish_project_event = AsyncMock()
    manager.consume = AsyncMock(return_value=[])
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
    """Create a mock BSVibeUser with admin role."""
    return _make_mock_user("admin")


@pytest_asyncio.fixture
async def test_app():
    """Create a test app instance with CORS disabled (empty origins)."""
    return create_app(cors_origins=[], rate_limit=False)


@pytest_asyncio.fixture
async def client(test_app, db_session, mock_stream_manager, mock_user):
    """Create a test HTTP client with overridden dependencies."""

    async def override_get_db():
        yield db_session

    async def override_get_current_user():
        return mock_user

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_current_user] = override_get_current_user
    test_app.state.stream_manager = mock_stream_manager
    test_app.state.orchestrators = {}

    # Disable rate limiting in tests to prevent cross-test interference
    test_app.state.rate_limit_disabled = True

    # NOTE: async_session is already patched via the db_session fixture,
    # so finalize_design's fresh-session write scope uses the test DB.

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as test_client:
        yield test_client

    test_app.dependency_overrides.clear()
    # Clean up app.state set by this fixture to prevent leakage
    if hasattr(test_app.state, "stream_manager"):
        del test_app.state.stream_manager
    if hasattr(test_app.state, "rate_limit_disabled"):
        del test_app.state.rate_limit_disabled
    if hasattr(test_app.state, "orchestrators"):
        del test_app.state.orchestrators
