import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.src.api import (
    agent_chat,
    agent_templates,
    agents,
    auth,
    budget,
    channels,
    dashboard,
    design,
    executor_configs,
    goals,
    import_project,
    mcp,
    memory,
    plan_tree,
    planner,
    projects,
    security,
    settings,
    tasks,
    workers,
    workspace,
)
from backend.src.config import Settings, settings as app_settings
from backend.src.core.channel_supervisor import (
    start_channel_supervisor,
    stop_channel_supervisor,
)
from backend.src.core.global_dispatcher import start_global_dispatcher, stop_global_dispatcher
from backend.src.core.rate_limiter import RateLimitMiddleware
from backend.src.core.security_headers import SecurityHeadersMiddleware
from backend.src.core.tenant_context import TenantMiddleware
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

    # State machine-specific log (transitions, escalation — easy to grep)
    state_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "state_machine.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    state_handler.setFormatter(formatter)
    logging.getLogger("backend.src.core.state_machine").addHandler(state_handler)


_setup_logging()


_DEV_SIGNING_KEY = Settings.model_fields["prompt_signing_key"].default
_DEV_ENCRYPTION_KEY = Settings.model_fields["encryption_key"].default


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage server lifecycle: startup and shutdown."""
    # Startup — security gate
    if not app_settings.debug and app_settings.prompt_signing_key == _DEV_SIGNING_KEY:
        raise RuntimeError(
            "FATAL: prompt_signing_key is still the dev default. "
            "Set a secure PROMPT_SIGNING_KEY env var for production."
        )
    if not app_settings.debug and app_settings.encryption_key == _DEV_ENCRYPTION_KEY:
        raise RuntimeError(
            "FATAL: encryption_key is still the dev default. Set a secure ENCRYPTION_KEY env var for production."
        )

    await init_db()
    redis = await get_redis()
    stream_manager = RedisStreamManager(redis)
    await stream_manager.initialize_streams()
    app.state.redis = redis
    app.state.stream_manager = stream_manager
    await start_background_consumer(app)
    await start_global_dispatcher(app)
    await start_channel_supervisor(app)

    yield

    # Shutdown
    await stop_channel_supervisor(app)
    await stop_global_dispatcher(app)
    await close_redis()


_ROUTERS = [
    agent_chat.router,
    agent_templates.router,
    agents.router,
    budget.router,
    executor_configs.router,
    auth.router,
    goals.router,
    tasks.router,
    projects.router,
    plan_tree.router,
    design.router,
    memory.router,
    import_project.router,
    channels.router,
    dashboard.router,
    settings.router,
    security.router,
    planner.router,
    workers.router,
    workspace.router,
    mcp.router,
]


def create_app(
    *,
    cors_origins: list[str] | None = None,
    rate_limit: bool | None = None,
    enable_hsts: bool | None = None,
    hsts_max_age: int | None = None,
) -> FastAPI:
    """App factory — creates a configured FastAPI instance.

    Defaults are read from app_settings; kwargs override for testing.
    """
    _app = FastAPI(
        title="BSNexus",
        description="AI-Powered Development Manager",
        version="0.1.0",
        lifespan=lifespan,
        redirect_slashes=False,
    )

    # Security headers (outermost — runs first on response)
    _app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=enable_hsts if enable_hsts is not None else app_settings.enable_hsts,
        hsts_max_age=hsts_max_age if hsts_max_age is not None else app_settings.hsts_max_age,
    )

    # Rate limiting
    if rate_limit if rate_limit is not None else app_settings.rate_limit_enabled:
        _app.add_middleware(RateLimitMiddleware)

    # CORS
    origins = cors_origins if cors_origins is not None else app_settings.cors_allowed_origins
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Tenant context — stamps request.state.tenant_id from the JWT.
    _app.add_middleware(TenantMiddleware)

    # Health endpoints
    @_app.get("/health")
    async def health():
        return {"status": "healthy", "version": "0.1.0"}

    @_app.get("/health/deps")
    async def health_deps():
        redis_status = "disconnected"
        try:
            redis = await get_redis()
            await redis.ping()  # type: ignore[misc]
            redis_status = "connected"
        except Exception:
            pass

        pg_status = "disconnected"
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                pg_status = "connected"
        except Exception:
            pass

        return {"redis": redis_status, "postgresql": pg_status}

    # API routers
    for router in _ROUTERS:
        _app.include_router(router)

    return _app


# Default app instance for uvicorn
app = create_app()
