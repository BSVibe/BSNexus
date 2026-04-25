"""S2-1 M19/H13: DB connection pool must be configurable via settings.

The default pool was hardcoded (pool_size=10, max_overflow=20). With
agents holding a session for up to 30 minutes during long LLM calls,
production deployments need to bump these without patching source.
"""

from __future__ import annotations

import pytest


def test_settings_expose_db_pool_knobs():
    """All four pool-shape knobs must be on Settings as overridable
    fields with sensible defaults.
    """
    from backend.src.config import Settings

    s = Settings()
    assert hasattr(s, "db_pool_size")
    assert hasattr(s, "db_max_overflow")
    assert hasattr(s, "db_pool_timeout_s")
    assert hasattr(s, "db_pool_recycle_s")

    # Defaults: keep current behavior.
    assert s.db_pool_size == 10
    assert s.db_max_overflow == 20
    assert s.db_pool_timeout_s == 60
    # ``-1`` (SQLAlchemy default) means "do not recycle". Operators bump
    # this when running behind a connection-killing proxy (e.g. PgBouncer
    # pause / RDS proxy idle drop).
    assert s.db_pool_recycle_s == -1


def test_settings_accept_env_overrides(monkeypatch: pytest.MonkeyPatch):
    """Env vars override the defaults — the M19/H13 fix outcome."""
    monkeypatch.setenv("DB_POOL_SIZE", "30")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "60")
    monkeypatch.setenv("DB_POOL_TIMEOUT_S", "120")
    monkeypatch.setenv("DB_POOL_RECYCLE_S", "1800")

    from backend.src.config import Settings

    s = Settings()
    assert s.db_pool_size == 30
    assert s.db_max_overflow == 60
    assert s.db_pool_timeout_s == 120
    assert s.db_pool_recycle_s == 1800


def test_create_engine_threads_pool_settings_into_sqlalchemy():
    """``create_db_engine`` must apply the configured knobs.

    A factory-style helper lets tests build the engine fresh after
    overriding settings, without touching the module-global engine
    used by the running app.
    """
    from backend.src.config import Settings
    from backend.src.storage.database import create_db_engine

    settings_obj = Settings(
        database_url="postgresql+asyncpg://x:y@h:5432/db",
        db_pool_size=15,
        db_max_overflow=25,
        db_pool_timeout_s=90,
        db_pool_recycle_s=300,
    )
    engine = create_db_engine(settings_obj)
    # SQLAlchemy stores pool config on the engine's pool instance.
    assert engine.pool.size() == 15
    # Per SQLAlchemy: max_overflow is on _max_overflow attribute.
    assert engine.pool._max_overflow == 25
    assert engine.pool._timeout == 90
    assert engine.pool._recycle == 300
