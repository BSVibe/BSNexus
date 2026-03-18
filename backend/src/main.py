import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.src.api import (
    architect,
    board,
    dashboard,
    pm,
    projects,
    security,
    settings,
    tasks,
)
from backend.src.config import settings as app_settings
from backend.src.core.rate_limiter import RateLimitMiddleware
from backend.src.core.security_headers import SecurityHeadersMiddleware
from backend.src.queue.background import start_background_consumer
from backend.src.queue.streams import RedisStreamManager
from backend.src.storage.database import init_db, engine
from backend.src.storage.redis_client import get_redis, close_redis


def _setup_logging() -> None:
    """Configure logging with both console and rotating file handlers."""
    log_level = getattr(logging, app_settings.log_level.upper(), logging.INFO)
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = logging.Formatter(log_format)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handlers — skip when running under pytest to avoid side effects
    if os.environ.get("TESTING"):
        return

    # File handler — rotating, 10MB per file, keep 5 backups
    log_dir = app_settings.log_dir
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "bsnexus.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Orchestrator-specific log (escalation, scheduling, results — easy to grep)
    orchestrator_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "orchestrator.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    orchestrator_handler.setFormatter(formatter)
    logging.getLogger("backend.src.core.orchestrator").addHandler(orchestrator_handler)
    logging.getLogger("backend.src.core.state_machine").addHandler(orchestrator_handler)


_setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage server lifecycle: startup and shutdown."""
    # Startup
    await init_db()
    redis = await get_redis()
    stream_manager = RedisStreamManager(redis)
    await stream_manager.initialize_streams()
    app.state.redis = redis
    app.state.stream_manager = stream_manager
    await start_background_consumer(app)

    yield

    # Shutdown
    await close_redis()


app = FastAPI(
    title="BSNexus",
    description="AI-Powered Development Manager",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# Security headers (outermost — runs first on response)
app.add_middleware(
    SecurityHeadersMiddleware,
    enable_hsts=app_settings.enable_hsts,
    hsts_max_age=app_settings.hsts_max_age,
)

# Rate limiting
if app_settings.rate_limit_enabled:
    app.add_middleware(RateLimitMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.1.0"}


@app.get("/health/deps")
async def health_deps():
    # Check Redis
    redis_status = "disconnected"
    try:
        redis = await get_redis()
        await redis.ping()  # type: ignore[misc]
        redis_status = "connected"
    except Exception:
        pass

    # Check PostgreSQL
    pg_status = "disconnected"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            pg_status = "connected"
    except Exception:
        pass

    return {
        "redis": redis_status,
        "postgresql": pg_status,
    }


# API routers
app.include_router(tasks.router)
app.include_router(projects.router)
app.include_router(pm.router)
app.include_router(architect.router)
app.include_router(board.router)
app.include_router(dashboard.router)
app.include_router(settings.router)
app.include_router(security.router)
