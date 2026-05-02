from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.src.config import Settings, settings


def create_db_engine(cfg: Settings) -> AsyncEngine:
    """Build the async SQLAlchemy engine from settings.

    Pool knobs are configurable via env (S2-1 M19/H13):
      * ``DB_POOL_SIZE`` (default 10)
      * ``DB_MAX_OVERFLOW`` (default 20)
      * ``DB_POOL_TIMEOUT_S`` (default 60)
      * ``DB_POOL_RECYCLE_S`` (default -1 = no recycle)

    The delegation chain can dispatch many agents simultaneously, each
    holding a session for an entire LLM call (up to 30 min). When that
    pressure exceeds 30 concurrent sessions the previous hardcoded
    pool starved the request path. Operators now bump these without
    patching source.
    """
    return create_async_engine(
        cfg.database_url,
        echo=cfg.debug,
        pool_size=cfg.db_pool_size,
        max_overflow=cfg.db_max_overflow,
        pool_timeout=cfg.db_pool_timeout_s,
        pool_recycle=cfg.db_pool_recycle_s,
        pool_pre_ping=True,
    )


engine = create_db_engine(settings)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session


async def init_db():
    """Create all tables if they don't exist (useful for SQLite dev mode)."""
    if engine.url.get_backend_name() == "sqlite":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
