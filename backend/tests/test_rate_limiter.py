"""RateLimiter — Redis-backed sliding-window + middleware wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.src.core.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    RateLimitMiddleware,
    _get_client_id,
)


def test_config_window_and_max():
    cfg = RateLimitConfig(requests_per_second=5.0, burst_size=10)
    assert cfg.max_requests == 10
    assert cfg.window_seconds == 2

    fast = RateLimitConfig(requests_per_second=100.0, burst_size=10)
    assert fast.window_seconds == 1  # clamp to 1


def test_get_config_picks_most_specific_prefix():
    limiter = RateLimiter(
        rate_limits={
            "/api": RateLimitConfig(burst_size=60),
            "/api/v1/settings": RateLimitConfig(burst_size=10),
        }
    )
    assert limiter._get_config("/api/v1/settings/projects").burst_size == 10
    assert limiter._get_config("/api/v1/projects").burst_size == 60
    assert limiter._get_config("/unmatched").burst_size == 20  # default


def test_get_bucket_key_encodes_prefix():
    limiter = RateLimiter(rate_limits={"/api/v1/settings": RateLimitConfig()})
    key = limiter._get_bucket_key("1.2.3.4", "/api/v1/settings/x")
    assert "rl:1.2.3.4:/api/v1/settings" == key

    fallback = limiter._get_bucket_key("1.2.3.4", "/no-match")
    assert fallback.endswith(":default")


def test_set_redis_resets_script_sha():
    limiter = RateLimiter()
    assert not limiter.has_redis
    limiter._script_sha = "fake-sha"
    limiter.set_redis(MagicMock())
    assert limiter.has_redis
    assert limiter._script_sha is None


@pytest.mark.asyncio
async def test_check_no_redis_allows_everything():
    limiter = RateLimiter()
    allowed, retry = await limiter.check("abc", "/api/v1/x")
    assert allowed is True
    assert retry == 0.0


@pytest.mark.asyncio
async def test_check_allowed_path_runs_lua_script():
    redis = MagicMock()
    redis.script_load = AsyncMock(return_value="sha-1")
    redis.evalsha = AsyncMock(return_value=[1, 0])
    limiter = RateLimiter(redis=redis)
    allowed, retry = await limiter.check("1.2.3.4", "/api/v1/projects")
    assert allowed is True
    assert retry == 0.0
    redis.script_load.assert_awaited_once()
    redis.evalsha.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_rejected_returns_retry_after():
    redis = MagicMock()
    redis.script_load = AsyncMock(return_value="sha-1")
    redis.evalsha = AsyncMock(return_value=[0, 1500])  # retry in 1.5s
    limiter = RateLimiter(redis=redis)
    allowed, retry = await limiter.check("1.2.3.4", "/api/v1/projects")
    assert allowed is False
    assert retry == 1.5


@pytest.mark.asyncio
async def test_check_redis_failure_fails_open():
    redis = MagicMock()
    redis.script_load = AsyncMock(side_effect=RuntimeError("boom"))
    limiter = RateLimiter(redis=redis)
    allowed, retry = await limiter.check("1.2.3.4", "/api/v1/projects")
    assert allowed is True
    assert retry == 0.0


def test_get_client_id_prefers_forwarded_for():
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"10.0.0.1, 10.0.0.2")],
        "client": ("127.0.0.1", 12345),
    }
    req = Request(scope)
    assert _get_client_id(req) == "10.0.0.1"


def test_get_client_id_falls_back_to_client_host():
    scope = {
        "type": "http",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }
    req = Request(scope)
    assert _get_client_id(req) == "127.0.0.1"


def test_get_client_id_unknown_when_no_info():
    scope = {"type": "http", "headers": []}
    req = Request(scope)
    assert _get_client_id(req) == "unknown"


def _build_app_with_middleware(rate_limiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI()
    mw = RateLimitMiddleware(app, rate_limiter=rate_limiter)

    @app.get("/api/v1/hello")
    def _hello():
        return {"ok": True}

    @app.get("/health")
    def _health():
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware, rate_limiter=mw.rate_limiter)
    return app


def test_middleware_bypasses_exempt_paths():
    app = _build_app_with_middleware()
    client = TestClient(app)
    assert client.get("/health").status_code == 200


def test_middleware_bypasses_when_state_flag_set():
    app = FastAPI()

    @app.get("/api/v1/hello")
    def _hello():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware)
    app.state.rate_limit_disabled = True
    client = TestClient(app)
    assert client.get("/api/v1/hello").status_code == 200


def test_middleware_returns_429_on_block():
    redis = MagicMock()
    redis.script_load = AsyncMock(return_value="sha-1")
    redis.evalsha = AsyncMock(return_value=[0, 500])
    limiter = RateLimiter(redis=redis)

    app = FastAPI()

    @app.get("/api/v1/hello")
    def _hello():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, rate_limiter=limiter)
    client = TestClient(app)
    resp = client.get("/api/v1/hello")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "1"


def test_middleware_allows_when_limiter_passes():
    redis = MagicMock()
    redis.script_load = AsyncMock(return_value="sha-1")
    redis.evalsha = AsyncMock(return_value=[1, 0])
    limiter = RateLimiter(redis=redis)

    app = FastAPI()

    @app.get("/api/v1/hello")
    def _hello():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, rate_limiter=limiter)
    client = TestClient(app)
    assert client.get("/api/v1/hello").status_code == 200
