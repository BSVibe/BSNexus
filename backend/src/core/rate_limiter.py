"""Rate limiting middleware for API protection."""

import time
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting a single client."""

    tokens: float
    max_tokens: float
    refill_rate: float  # tokens per second
    last_refill: float = field(default_factory=time.monotonic)

    def consume(self) -> bool:
        """Try to consume a token. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    @property
    def retry_after(self) -> float:
        """Seconds until a token is available."""
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.refill_rate


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit rule."""

    requests_per_second: float = 10.0
    burst_size: int = 20

    @property
    def refill_rate(self) -> float:
        return self.requests_per_second


# Default rate limits by path prefix
DEFAULT_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "/api/v1/architect": RateLimitConfig(requests_per_second=5.0, burst_size=10),
    "/api/v1/settings": RateLimitConfig(requests_per_second=5.0, burst_size=10),
    "/api/v1/registration-tokens": RateLimitConfig(requests_per_second=5.0, burst_size=10),
    "/health": RateLimitConfig(requests_per_second=20.0, burst_size=40),
    "/api": RateLimitConfig(requests_per_second=30.0, burst_size=60),
}


class RateLimiter:
    """In-memory token bucket rate limiter.

    Limitations:
    - Single-process only: with multiple uvicorn workers, each process has its
      own bucket dict, effectively multiplying the rate limit by the worker count.
    - Memory: stale buckets are cleaned every 5 minutes, but sustained traffic
      from many distinct IPs will grow the dict proportionally.

    TODO: Replace with Redis-backed implementation (e.g. sliding window counter
    via EVALSHA) for production multi-worker deployments.
    """

    def __init__(self, rate_limits: dict[str, RateLimitConfig] | None = None) -> None:
        self.rate_limits = rate_limits or DEFAULT_RATE_LIMITS
        self._buckets: dict[str, RateLimitBucket] = {}
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300.0  # Clean up stale buckets every 5 minutes

    def _get_config(self, path: str) -> RateLimitConfig:
        """Find the most specific rate limit config for a path."""
        best_match = ""
        best_config = RateLimitConfig()  # default fallback

        for prefix, config in self.rate_limits.items():
            if path.startswith(prefix) and len(prefix) > len(best_match):
                best_match = prefix
                best_config = config

        return best_config

    def _get_bucket_key(self, client_id: str, path: str) -> str:
        """Generate a bucket key from client ID and matched path prefix."""
        config_prefix = ""
        best_len = 0
        for prefix in self.rate_limits:
            if path.startswith(prefix) and len(prefix) > best_len:
                config_prefix = prefix
                best_len = len(prefix)
        return f"{client_id}:{config_prefix or 'default'}"

    def _cleanup_stale_buckets(self) -> None:
        """Remove buckets that haven't been used recently."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        stale_keys = [key for key, bucket in self._buckets.items() if now - bucket.last_refill > self._cleanup_interval]
        for key in stale_keys:
            del self._buckets[key]

    def check(self, client_id: str, path: str) -> tuple[bool, float]:
        """Check if a request is allowed.

        Returns:
            Tuple of (allowed, retry_after_seconds).
        """
        self._cleanup_stale_buckets()

        config = self._get_config(path)
        bucket_key = self._get_bucket_key(client_id, path)

        if bucket_key not in self._buckets:
            self._buckets[bucket_key] = RateLimitBucket(
                tokens=float(config.burst_size),
                max_tokens=float(config.burst_size),
                refill_rate=config.refill_rate,
            )

        bucket = self._buckets[bucket_key]
        allowed = bucket.consume()
        return allowed, bucket.retry_after


def _get_client_id(request: Request) -> str:
    """Extract client identifier from request."""
    # Use X-Forwarded-For if behind a reverse proxy, otherwise use client host
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

        client_id = _get_client_id(request)
        allowed, retry_after = self.rate_limiter.check(client_id, request.url.path)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please retry later."},
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        response = await call_next(request)
        return response
