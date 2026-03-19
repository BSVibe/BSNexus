"""Security headers middleware for web security hardening."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to all HTTP responses.

    Implements OWASP recommended security headers for XSS prevention,
    clickjacking protection, content-type sniffing prevention, and more.
    """

    def __init__(
        self,
        app,
        *,
        hsts_max_age: int = 31536000,
        hsts_include_subdomains: bool = True,
        hsts_preload: bool = False,
        content_security_policy: str | None = None,
        permissions_policy: str | None = None,
        enable_hsts: bool = False,
    ) -> None:
        super().__init__(app)
        self.hsts_max_age = hsts_max_age
        self.hsts_include_subdomains = hsts_include_subdomains
        self.hsts_preload = hsts_preload
        self.enable_hsts = enable_hsts
        self.content_security_policy = (
            content_security_policy or "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
        )
        self.permissions_policy = permissions_policy or "camera=(), microphone=(), geolocation=(), payment=()"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        # Prevent XSS attacks
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy
        response.headers["Content-Security-Policy"] = self.content_security_policy

        # Permissions Policy (formerly Feature-Policy)
        response.headers["Permissions-Policy"] = self.permissions_policy

        # Prevent caching of API responses (skip if Cache-Control is already set by the handler)
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, proxy-revalidate"
            response.headers["Pragma"] = "no-cache"

        # HSTS (only enable when serving over HTTPS in production)
        if self.enable_hsts:
            hsts_value = f"max-age={self.hsts_max_age}"
            if self.hsts_include_subdomains:
                hsts_value += "; includeSubDomains"
            if self.hsts_preload:
                hsts_value += "; preload"
            response.headers["Strict-Transport-Security"] = hsts_value

        # Prevent MIME type confusion attacks
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        return response
