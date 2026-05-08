import faulthandler
import logging
import logging.handlers
import os
import signal
import sys
from contextlib import asynccontextmanager

from bsvibe_core import configure_logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.src.api import (
    auth,
    brief,
    conversation,
    decisions as decisions_api,
    deliverables,
    executor_configs,
    inside,
    integrations,
    project_events,
    projects,
    requests_api,
    run_summaries,
    workspace_files,
)
from backend.src.config import settings as app_settings
from backend.src.core.rate_limiter import RateLimitMiddleware
from backend.src.core.security_headers import SecurityHeadersMiddleware
from backend.src.core.startup_guards import enforce_production_security_guards
from backend.src.core.tenant_context import TenantMiddleware
from backend.src.queue.background import start_background_consumer
from backend.src.queue.streams import RedisStreamManager
from backend.src.storage.database import init_db, engine
from backend.src.storage.redis_client import get_redis, close_redis


def _setup_logging() -> None:
    """Configure logging with both console and rotating file handlers.

    Phase A Batch 5: structured JSON via ``bsvibe_core.configure_logging``
    is the canonical channel (production wire format shared with the
    three sibling products). Stdlib ``logging`` is still configured so
    third-party libraries (FastAPI, SQLAlchemy, etc.) and the rotating
    file handler keep working — the two pipelines coexist.
    """
    log_level = getattr(logging, app_settings.log_level.upper(), logging.INFO)
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = logging.Formatter(log_format)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Standardised structlog pipeline — JSON in production, ConsoleRenderer
    # for local dev when LOG_LEVEL=debug or DEBUG=1 to keep `pytest -s`
    # readable. Tests use TESTING env to keep stdout pristine.
    json_output = not bool(os.environ.get("DEBUG") or app_settings.debug)
    configure_logging(
        level=app_settings.log_level,
        json_output=json_output,
        service_name="bsnexus",
    )

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

    RunOrchestrator is event-driven (dispatched per Run), not a
    background task — there's nothing to start here for it.
    """
    import asyncio as _asyncio_local

    _install_thread_traceback_signal()
    _install_asyncio_signal(_asyncio_local.get_running_loop())

    # S1-3 H9 / M19: refuse to start when ENVIRONMENT=production but dev
    # defaults (signing key, encryption key, frontend_url) are still in
    # place. Replaces the prior ``not debug`` heuristic — see
    # ``core/startup_guards.py`` for the rationale.
    enforce_production_security_guards(app_settings)

    await init_db()
    redis = await get_redis()
    stream_manager = RedisStreamManager(redis)
    await stream_manager.initialize_streams()
    app.state.redis = redis
    app.state.stream_manager = stream_manager
    await start_background_consumer(app)

    from backend.src.storage.database import async_session

    # Direction reset 2026-05-03 — BSNexus no longer hosts workers; the
    # worker_result_consumer that drained runs:results into
    # on_run_completed is gone. BSGateway-dispatched runs complete
    # synchronously via the chat completion stream (BSGatewayClient).

    # Phase Audit Batch 2 — bsvibe-audit OutboxRelay. Reads
    # ``audit_outbox`` rows that domain code wrote inside their own
    # transactions and ships them to BSVibe-Auth. Disabled (no-op
    # singleton) when ``BSVIBE_AUTH_AUDIT_URL`` is empty — dev
    # environments boot without the audit destination configured and
    # outbox rows just queue up locally.
    from backend.src.core.audit import build_relay  # noqa: PLC0415

    audit_relay = build_relay(session_factory=async_session)
    await audit_relay.start()
    app.state.audit_relay = audit_relay

    # Direction reset 2026-05-03 — FastMCP transport's session manager
    # owns an internal task group that must be entered at process
    # startup. ``attach_to_app`` mounts the sub-app but Starlette mounts
    # don't auto-propagate sub-app lifespans, so we chain it here.
    # Without this every request to ``/mcp/http`` 500s with "Task group
    # is not initialized" and the LLM tool loop falls back to no-tools
    # mode (Round 1 finding 2026-05-07).
    from backend.src.mcp.server import fastmcp_session_manager_run  # noqa: PLC0415

    # Decision-locks A1 — boot the Verifier Worker if enabled. A
    # disabled worker is the degradable mode: new deliverables stay at
    # ``proof_state = verification_missing``. The worker is never
    # required for the API to serve.
    verifier_worker = None
    verifier_task: _asyncio_local.Task[None] | None = None
    if app_settings.verifier_enabled:
        from backend.src.core.verifier import (  # noqa: PLC0415
            SubprocessVerifier,
            default_registry,
        )
        from backend.src.workers.verifier_worker import (  # noqa: PLC0415
            start_verifier_worker_task,
        )

        if not default_registry.supported_types():
            default_registry.register(SubprocessVerifier())

        verifier_worker, verifier_task = await start_verifier_worker_task(
            registry=default_registry,
            stream_manager=stream_manager,
            session_factory=async_session,
        )
        app.state.verifier_worker = verifier_worker
        app.state.verifier_task = verifier_task

    try:
        async with fastmcp_session_manager_run():
            yield
    finally:
        if verifier_worker is not None:
            verifier_worker.stop()
        if verifier_task is not None:
            try:
                await _asyncio_local.wait_for(verifier_task, timeout=5)
            except _asyncio_local.TimeoutError:
                verifier_task.cancel()
        await audit_relay.stop()
        await close_redis()


_ROUTERS = [
    auth.router,
    projects.router,
    conversation.router,
    requests_api.router,
    deliverables.router,
    decisions_api.router,
    brief.router,
    inside.runs_router,
    inside.snapshot_router,
    integrations.router,
    executor_configs.router,
    workspace_files.router,
    project_events.router,
    run_summaries.router,
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

    # Direction reset 2026-05-03 — BSNexus MCP server. ``/mcp/health``
    # is registered as a normal router; ``/mcp/http`` is a Starlette
    # ASGI mount (streamable-HTTP transport) because FastMCP returns
    # its own ASGI app.
    from backend.src.mcp.server import (  # noqa: PLC0415
        admin_router as mcp_admin_router,
        attach_to_app,
        router as mcp_router,
    )

    _app.include_router(mcp_router)
    _app.include_router(mcp_admin_router)
    attach_to_app(_app)

    # ─── Demo mode (separate deployment, BSVIBE_DEMO_MODE=true) ─────────
    # Demo backend exposes /api/v1/demo/session and swaps the prod
    # tenant_id dep for one that reads from the demo JWT. Prod backends
    # never reach this branch.
    from bsvibe_demo import is_demo_mode  # noqa: PLC0415

    if is_demo_mode():
        from backend.src.core.auth import get_current_user  # noqa: PLC0415
        from backend.src.core.tenant_context import get_tenant_id  # noqa: PLC0415
        from backend.src.demo.auth import (  # noqa: PLC0415
            demo_get_current_user,
            demo_tenant_id,
        )
        from backend.src.demo.router import demo_router  # noqa: PLC0415

        _app.include_router(demo_router)
        _app.dependency_overrides[get_tenant_id] = demo_tenant_id
        # Without this override, every authed handler still calls
        # auth.bsvibe.dev's JWKS verifier and the HS256 demo JWT fails
        # with "Unable to find a signing key" — UI loads but every
        # /api/v1/projects, /messages, /requests fetch returns 401.
        _app.dependency_overrides[get_current_user] = demo_get_current_user

    return _app


# Default app instance for uvicorn
app = create_app()
