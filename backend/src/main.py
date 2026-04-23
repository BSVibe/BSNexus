import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.src.api import (
    auth,
    conversation,
    decisions as decisions_api,
    deliverables,
    executor_configs,
    inside,
    integrations,
    projects,
    requests_api,
    workers as workers_api,
)
from backend.src.config import Settings, settings as app_settings
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

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if os.environ.get("TESTING"):
        return

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


_setup_logging()


_DEV_SIGNING_KEY = Settings.model_fields["prompt_signing_key"].default
_DEV_ENCRYPTION_KEY = Settings.model_fields["encryption_key"].default


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server lifecycle: startup and shutdown.

    P3 TODO: start RunOrchestrator (event-driven, replaces GlobalDispatcher).
    """
    if not app_settings.debug and app_settings.prompt_signing_key == _DEV_SIGNING_KEY:
        raise RuntimeError(
            "FATAL: prompt_signing_key is still the dev default. "
            "Set a secure PROMPT_SIGNING_KEY env var for production."
        )
    if not app_settings.debug and app_settings.encryption_key == _DEV_ENCRYPTION_KEY:
        raise RuntimeError(
            "FATAL: encryption_key is still the dev default. Set a secure "
            "ENCRYPTION_KEY env var for production."
        )

    await init_db()
    redis = await get_redis()
    stream_manager = RedisStreamManager(redis)
    await stream_manager.initialize_streams()
    app.state.redis = redis
    app.state.stream_manager = stream_manager
    await start_background_consumer(app)

    # Drain runs:results → orchestrator.on_run_completed. Closes the
    # loop for worker-executed runs so they don't stay stuck in
    # ``running`` forever.
    from backend.src.queue.worker_result_consumer import WorkerResultConsumer
    from backend.src.storage.database import async_session

    worker_result_consumer = WorkerResultConsumer(
        stream_manager=stream_manager, session_maker=async_session
    )
    await worker_result_consumer.start()
    app.state.worker_result_consumer = worker_result_consumer

    try:
        yield
    finally:
        await worker_result_consumer.stop()
        await close_redis()


_ROUTERS = [
    auth.router,
    projects.router,
    conversation.router,
    requests_api.router,
    deliverables.router,
    decisions_api.project_router,
    decisions_api.decision_router,
    inside.runs_router,
    inside.snapshot_router,
    integrations.router,
    executor_configs.router,
    workers_api.router,
]


def create_app(
    *,
    cors_origins: list[str] | None = None,
    rate_limit: bool | None = None,
    enable_hsts: bool | None = None,
    hsts_max_age: int | None = None,
) -> FastAPI:
    """App factory — creates a configured FastAPI instance."""
    _app = FastAPI(
        title="BSNexus",
        description="Shell for the AI company you hired",
        version="0.2.0",
        lifespan=lifespan,
        redirect_slashes=False,
    )

    _app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=enable_hsts if enable_hsts is not None else app_settings.enable_hsts,
        hsts_max_age=hsts_max_age if hsts_max_age is not None else app_settings.hsts_max_age,
    )

    if rate_limit if rate_limit is not None else app_settings.rate_limit_enabled:
        _app.add_middleware(RateLimitMiddleware)

    origins = cors_origins if cors_origins is not None else app_settings.cors_allowed_origins
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _app.add_middleware(TenantMiddleware)

    @_app.get("/health")
    async def health():
        return {"status": "healthy", "version": "0.2.0"}

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

    for router in _ROUTERS:
        _app.include_router(router)

    return _app


# Default app instance for uvicorn
app = create_app()
