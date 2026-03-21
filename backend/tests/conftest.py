from __future__ import annotations

import os

os.environ.setdefault("TESTING", "1")

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.src.main import app
from backend.src.storage.database import Base, get_db

# Import models so Base.metadata.create_all picks them up.
# Security models (audit_logger, access_control, compliance) are loaded
# transitively via main.py -> security router imports.
import backend.src.models  # noqa: F401

TEST_DATABASE_URL = "sqlite+aiosqlite://"


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
    """Create a test database session.

    Also patches async_session in the architect module so that
    finalize_design's fresh-session write scope uses the test DB.
    """
    with patch("backend.src.api.architect.async_session", test_session_maker):
        async with test_session_maker() as session:
            yield session


@pytest_asyncio.fixture
async def mock_stream_manager():
    """Create a mock RedisStreamManager."""
    manager = AsyncMock()
    manager.publish = AsyncMock(return_value="mock-message-id")
    manager.publish_board_event = AsyncMock()
    return manager


@pytest_asyncio.fixture
async def client(db_session, mock_stream_manager):
    """Create a test HTTP client with overridden dependencies."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.state.stream_manager = mock_stream_manager

    # Disable rate limiting in tests to prevent cross-test interference
    app.state.rate_limit_disabled = True

    # NOTE: async_session is already patched via the db_session fixture,
    # so finalize_design's fresh-session write scope uses the test DB.

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    # Clean up app.state set by this fixture to prevent leakage
    if hasattr(app.state, "stream_manager"):
        del app.state.stream_manager
    if hasattr(app.state, "rate_limit_disabled"):
        del app.state.rate_limit_disabled
