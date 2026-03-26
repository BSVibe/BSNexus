from collections.abc import AsyncGenerator

from backend.src.config import settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

engine = create_async_engine(settings.database_url, echo=settings.debug)
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
