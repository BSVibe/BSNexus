"""Rate limiting middleware for API protection.

Uses a Redis-backed sliding window counter for multi-worker / multi-process safety.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit rule."""

    requests_per_second: float = 10.0
    burst_size: int = 20

    @property
    def window_seconds(self) -> int:
        """Sliding window size derived from burst_size / rps (minimum 1s)."""
        return max(1, int(self.burst_size / self.requests_per_second))

    @property
    def max_requests(self) -> int:
        """Maximum requests allowed within the sliding window."""
        return self.burst_size


# Default rate limits by path prefix
DEFAULT_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "/api/v1/architect": RateLimitConfig(requests_per_second=5.0, burst_size=10),
    "/api/v1/settings": RateLimitConfig(requests_per_second=5.0, burst_size=10),
    "/api/v1/registration-tokens": RateLimitConfig(requests_per_second=5.0, burst_size=10),
    "/health": RateLimitConfig(requests_per_second=20.0, burst_size=40),
    "/api": RateLimitConfig(requests_per_second=30.0, burst_size=60),
}


# Lua script for atomic sliding window check-and-increment.
# KEYS[1] = sorted-set key, ARGV = [now_ms, window_start_ms, max_requests, window_seconds]
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_start_ms = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local window_seconds = tonumber(ARGV[4])

-- Remove entries outside the window
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start_ms)

-- Count current entries
local count = redis.call('ZCARD', key)

if count < max_requests then
    -- Add current request
    redis.call('ZADD', key, now_ms, now_ms .. ':' .. math.random(1000000))
    redis.call('EXPIRE', key, window_seconds + 1)
    return {1, 0}  -- allowed=1, retry_after=0
else
    -- Rejected: compute retry_after from oldest entry
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_ms = 0
    if #oldest >= 2 then
        retry_ms = tonumber(oldest[2]) + (window_seconds * 1000) - now_ms
        if retry_ms < 0 then retry_ms = 0 end
    end
    return {0, retry_ms}
end
"""


class RateLimiter:
    """Redis-backed sliding window rate limiter.

    Each (client, path-prefix) pair gets a Redis sorted set.
    Members are timestamped request entries; the window slides
    forward continuously, so the limit is enforced across all workers.
    """

    def __init__(
        self,
        rate_limits: dict[str, RateLimitConfig] | None = None,
        redis: Redis | None = None,
    ) -> None:
        self.rate_limits = rate_limits or DEFAULT_RATE_LIMITS
        self._redis: Redis | None = redis
        self._script_sha: str | None = None

    @property
    def has_redis(self) -> bool:
        """Whether a Redis connection has been attached."""
        return self._redis is not None

    def set_redis(self, redis: Redis) -> None:
        """Attach a Redis connection (called once at startup)."""
        self._redis = redis
        self._script_sha = None

    def _get_config(self, path: str) -> RateLimitConfig:
        """Find the most specific rate limit config for a path."""
        best_match = ""
        best_config = RateLimitConfig()

        for prefix, config in self.rate_limits.items():
            if path.startswith(prefix) and len(prefix) > len(best_match):
                best_match = prefix
                best_config = config

        return best_config

    def _get_bucket_key(self, client_id: str, path: str) -> str:
        """Generate a Redis key from client ID and matched path prefix."""
        config_prefix = ""
        best_len = 0
        for prefix in self.rate_limits:
            if path.startswith(prefix) and len(prefix) > best_len:
                config_prefix = prefix
                best_len = len(prefix)
        return f"rl:{client_id}:{config_prefix or 'default'}"

    async def check(self, client_id: str, path: str) -> tuple[bool, float]:
        """Check if a request is allowed.

        Returns:
            Tuple of (allowed, retry_after_seconds).
        """
        if self._redis is None:
            # Fallback: allow everything if Redis is not available
            return True, 0.0

        config = self._get_config(path)
        bucket_key = self._get_bucket_key(client_id, path)
        now_ms = int(time.time() * 1000)
        window_start_ms = now_ms - (config.window_seconds * 1000)

        try:
            if self._script_sha is None:
                self._script_sha = await self._redis.script_load(_SLIDING_WINDOW_LUA)

            result = await self._redis.evalsha(
                self._script_sha,
                1,
                bucket_key,
                str(now_ms),
                str(window_start_ms),
                str(config.max_requests),
                str(config.window_seconds),
            )

            allowed = bool(result[0])
            retry_after_ms = int(result[1])
            return allowed, retry_after_ms / 1000.0

        except Exception:
            # Redis failure: degrade gracefully — allow the request
            logger.warning("rate_limiter_redis_error", client_id=client_id, path=path, exc_info=True)
            return True, 0.0


def _get_client_id(request: Request) -> str:
    """Extract client identifier from request."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client:
        return request.client.host

    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """HTTP middleware that enforces rate limits per client."""

    def __init__(
        self,
        app,
        *,
        rate_limiter: RateLimiter | None = None,
        exempt_paths: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.exempt_paths = exempt_paths or {"/health", "/health/deps", "/docs", "/openapi.json"}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Allow tests to disable rate limiting via app state
        app_state = getattr(request, "app", None)
        if app_state and getattr(getattr(app_state, "state", None), "rate_limit_disabled", False):
            return await call_next(request)

        # Skip rate limiting for exempt paths
        if request.url.path in self.exempt_paths:
            return await call_next(request)

        # Lazily attach Redis from app state if not already set
        if not self.rate_limiter.has_redis:
            redis_client = getattr(getattr(app_state, "state", None), "redis", None)
            if redis_client is not None:
                self.rate_limiter.set_redis(redis_client)

        client_id = _get_client_id(request)
        allowed, retry_after = await self.rate_limiter.check(client_id, request.url.path)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please retry later."},
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        response = await call_next(request)
        return response
