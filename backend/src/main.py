import faulthandler
import logging
import logging.handlers
import os
import signal
import sys
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
    project_events,
    projects,
    requests_api,
    workers as workers_api,
    workspace_files,
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


_TRACE_DUMP_PATH = os.getenv("BSNEXUS_TRACE_DUMP_PATH", "/tmp/bsnexus-trace.log")
_trace_dump_fp = None


def _install_thread_traceback_signal() -> None:
    """``kill -USR1 <pid>`` dumps every OS thread's Python stack to
    ``$BSNEXUS_TRACE_DUMP_PATH``. Synchronous-safe: faulthandler's
    handler is signal-safe and writes directly to the registered fd."""
    global _trace_dump_fp  # noqa: PLW0603 — single-shot startup setup
    _trace_dump_fp = open(_TRACE_DUMP_PATH, "a", buffering=1, encoding="utf-8")
    faulthandler.register(signal.SIGUSR1, file=_trace_dump_fp, all_threads=True, chain=False)


def _install_asyncio_signal(loop) -> None:
    """``kill -USR2 <pid>`` enumerates every asyncio task on the running
    loop. Must be installed via ``loop.add_signal_handler`` (not
    ``signal.signal``) so the callback runs inside the event loop —
    ``asyncio.all_tasks()`` and ``task.print_stack()`` aren't safe from
    a raw signal-handler frame."""
    import datetime as _dt
    import asyncio as _asyncio

    def _dump():
        if _trace_dump_fp is None:
            return
        tasks = _asyncio.all_tasks(loop)
        ts = _dt.datetime.now().isoformat(timespec="seconds")
        _trace_dump_fp.write(f"\n===== {ts} asyncio tasks: {len(tasks)} =====\n")
        for task in tasks:
            _trace_dump_fp.write(f"\n[{task.get_name()}] state={task._state}\n")
            try:
                task.print_stack(file=_trace_dump_fp)
            except Exception as exc:  # noqa: BLE001 — best effort
                _trace_dump_fp.write(f"  print_stack failed: {exc}\n")
        _trace_dump_fp.write("===== end asyncio tasks =====\n\n")
        _trace_dump_fp.flush()

    loop.add_signal_handler(signal.SIGUSR2, _dump)
    # ``sys`` is imported at module top — keep the reference live for
    # closures that may grow later.
    _ = sys


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server lifecycle: startup and shutdown.

    P3 TODO: start RunOrchestrator (event-driven, replaces GlobalDispatcher).
    """
    import asyncio as _asyncio_local

    _install_thread_traceback_signal()
    _install_asyncio_signal(_asyncio_local.get_running_loop())
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

    # Drain runs:results → orchestrator.on_run_completed. Closes the
    # loop for worker-executed runs so they don't stay stuck in
    # ``running`` forever.
    from backend.src.queue.worker_result_consumer import WorkerResultConsumer
    from backend.src.storage.database import async_session

    worker_result_consumer = WorkerResultConsumer(stream_manager=stream_manager, session_maker=async_session)
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
    workspace_files.router,
    project_events.router,
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
