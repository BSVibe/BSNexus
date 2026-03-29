"""Tests for Redis-backed RateLimiter and RateLimitMiddleware."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from backend.src.core.rate_limiter import RateLimitConfig, RateLimitMiddleware, RateLimiter


# ── RateLimiter unit tests ──────────────────────────────────────────


class TestRateLimiterNoRedis:
    """When Redis is not available, all requests should be allowed."""

    async def test_allows_without_redis(self):
        limiter = RateLimiter(rate_limits={"/api": RateLimitConfig(requests_per_second=10.0, burst_size=2)})
        # No Redis set — should degrade gracefully
        allowed, retry_after = await limiter.check("client-1", "/api/v1/tasks")
        assert allowed is True
        assert retry_after == 0.0


class TestRateLimiterWithRedis:
    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.script_load = AsyncMock(return_value="sha123")
        return redis

    async def test_allows_within_limit(self, mock_redis):
        mock_redis.evalsha = AsyncMock(return_value=[1, 0])
        limiter = RateLimiter(
            rate_limits={"/api": RateLimitConfig(requests_per_second=10.0, burst_size=5)},
            redis=mock_redis,
        )
        allowed, _ = await limiter.check("client-1", "/api/v1/tasks")
        assert allowed is True

    async def test_blocks_after_burst(self, mock_redis):
        mock_redis.evalsha = AsyncMock(return_value=[0, 500])
        limiter = RateLimiter(
            rate_limits={"/api": RateLimitConfig(requests_per_second=10.0, burst_size=2)},
            redis=mock_redis,
        )
        allowed, retry_after = await limiter.check("client-1", "/api/v1/tasks")
        assert allowed is False
        assert retry_after == 0.5

    async def test_different_clients_get_different_keys(self, mock_redis):
        mock_redis.evalsha = AsyncMock(return_value=[1, 0])
        limiter = RateLimiter(
            rate_limits={"/api": RateLimitConfig(requests_per_second=10.0, burst_size=1)},
            redis=mock_redis,
        )
        await limiter.check("client-1", "/api/v1/tasks")
        await limiter.check("client-2", "/api/v1/tasks")
        # Both should call evalsha with different keys
        assert mock_redis.evalsha.call_count == 2
        keys_used = [call.args[2] for call in mock_redis.evalsha.call_args_list]
        assert keys_used[0] != keys_used[1]

    async def test_path_specific_limits(self, mock_redis):
        mock_redis.evalsha = AsyncMock(return_value=[1, 0])
        limiter = RateLimiter(
            rate_limits={
                "/api/v1/architect": RateLimitConfig(requests_per_second=1.0, burst_size=1),
                "/api": RateLimitConfig(requests_per_second=100.0, burst_size=100),
            },
            redis=mock_redis,
        )
        await limiter.check("client-1", "/api/v1/architect/sessions")
        key_architect = mock_redis.evalsha.call_args_list[0].args[2]
        await limiter.check("client-1", "/api/v1/tasks")
        key_api = mock_redis.evalsha.call_args_list[1].args[2]
        assert "architect" in key_architect
        assert "architect" not in key_api

    async def test_redis_failure_degrades_gracefully(self, mock_redis):
        mock_redis.evalsha = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis.script_load = AsyncMock(return_value="sha123")
        limiter = RateLimiter(
            rate_limits={"/api": RateLimitConfig(requests_per_second=1.0, burst_size=1)},
            redis=mock_redis,
        )
        allowed, _ = await limiter.check("client-1", "/api/v1/tasks")
        assert allowed is True

    async def test_set_redis_attaches_connection(self):
        limiter = RateLimiter()
        assert limiter._redis is None
        mock_redis = AsyncMock()
        limiter.set_redis(mock_redis)
        assert limiter._redis is mock_redis
        assert limiter._script_sha is None  # Reset on new connection

    async def test_script_sha_cached(self, mock_redis):
        mock_redis.evalsha = AsyncMock(return_value=[1, 0])
        limiter = RateLimiter(
            rate_limits={"/api": RateLimitConfig(requests_per_second=10.0, burst_size=5)},
            redis=mock_redis,
        )
        await limiter.check("client-1", "/api/v1/tasks")
        await limiter.check("client-1", "/api/v1/tasks")
        # script_load called only once
        mock_redis.script_load.assert_awaited_once()


class TestRateLimitConfig:
    def test_window_seconds(self):
        config = RateLimitConfig(requests_per_second=10.0, burst_size=20)
        assert config.window_seconds == 2

    def test_window_seconds_minimum_one(self):
        config = RateLimitConfig(requests_per_second=100.0, burst_size=10)
        # 10 / 100 = 0.1 → rounded to 1
        assert config.window_seconds == 1

    def test_max_requests(self):
        config = RateLimitConfig(requests_per_second=5.0, burst_size=15)
        assert config.max_requests == 15


# ── Middleware integration tests ─────────────────────────────────────


class TestRateLimitMiddleware:
    @pytest.fixture
    def app(self):
        async def homepage(request: Request) -> JSONResponse:
            return JSONResponse({"ok": True})

        starlette_app = Starlette(
            routes=[
                Route("/", homepage),
                Route("/api/test", homepage),
                Route("/health", homepage),
            ]
        )
        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        mock_redis.evalsha = AsyncMock(return_value=[1, 0])
        limiter = RateLimiter(
            rate_limits={"/api": RateLimitConfig(requests_per_second=10.0, burst_size=2)},
            redis=mock_redis,
        )
        starlette_app.add_middleware(RateLimitMiddleware, rate_limiter=limiter, exempt_paths={"/health"})
        return starlette_app

    async def test_allows_normal_requests(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/test")
        assert resp.status_code == 200

    async def test_blocks_excessive_requests(self):
        """Middleware returns 429 when limiter denies."""

        async def homepage(request: Request) -> JSONResponse:
            return JSONResponse({"ok": True})

        starlette_app = Starlette(routes=[Route("/api/test", homepage)])
        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        mock_redis.evalsha = AsyncMock(return_value=[0, 1500])  # blocked
        limiter = RateLimiter(
            rate_limits={"/api": RateLimitConfig(requests_per_second=1.0, burst_size=1)},
            redis=mock_redis,
        )
        starlette_app.add_middleware(RateLimitMiddleware, rate_limiter=limiter)
        async with AsyncClient(transport=ASGITransport(app=starlette_app), base_url="http://test") as client:
            resp = await client.get("/api/test")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    async def test_exempt_paths_not_limited(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(10):
                resp = await client.get("/health")
                assert resp.status_code == 200

    async def test_rate_limit_disabled_via_state(self):
        """When rate_limit_disabled is True on app.state, requests pass through."""

        async def homepage(request: Request) -> JSONResponse:
            return JSONResponse({"ok": True})

        starlette_app = Starlette(routes=[Route("/api/test", homepage)])
        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        mock_redis.evalsha = AsyncMock(return_value=[0, 1000])  # would block
        limiter = RateLimiter(
            rate_limits={"/api": RateLimitConfig(requests_per_second=1.0, burst_size=1)},
            redis=mock_redis,
        )
        starlette_app.add_middleware(RateLimitMiddleware, rate_limiter=limiter)
        starlette_app.state.rate_limit_disabled = True
        async with AsyncClient(transport=ASGITransport(app=starlette_app), base_url="http://test") as client:
            resp = await client.get("/api/test")
        assert resp.status_code == 200


# -- Client identification ------------------------------------------------


class TestGetClientId:
    def test_x_forwarded_for_header(self):
        from backend.src.core.rate_limiter import _get_client_id

        request = MagicMock()
        request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        result = _get_client_id(request)
        assert result == "1.2.3.4"

    def test_unknown_when_no_client_or_header(self):
        from backend.src.core.rate_limiter import _get_client_id

        request = MagicMock()
        request.headers = {}
        request.client = None
        result = _get_client_id(request)
        assert result == "unknown"

    def test_client_host_fallback(self):
        from backend.src.core.rate_limiter import _get_client_id

        request = MagicMock()
        request.headers = {}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"
        result = _get_client_id(request)
        assert result == "10.0.0.1"
