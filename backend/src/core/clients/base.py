"""BaseServiceClient — shared HTTP scaffolding for sibling-service calls.

Consolidates the duplicated HTTP/auth/timeout patterns previously copied
between ``core/composer/knowledge_client.py``,
``core/audit/audit_sink.py``, and ``api/integrations.py`` (provider probe).

Decision #15 (Lockin): ``ServiceClientProtocol`` is a ``@runtime_checkable``
Protocol. Concrete adapters compose ``BaseServiceClient`` instead of
inheriting from an ABC — keeps Python idiomatic typing.

auth_provider boundary
----------------------
The Authorization header value comes from a caller-supplied callable.
Sync or async; the client awaits awaitable results so either works.

* Phase A (current): closure returns the tenant's static
  ``TenantIntegrationConfig.api_key``.
* Phase 0 P0.7 (planned): closure is swapped to a service-JWT minter.
  No adapter code changes — only the closure provided at construction.

Fail-soft contract
------------------
``request`` raises like a normal httpx call. ``safe_request`` swallows
network/HTTP errors and returns ``None`` so callers can degrade to a
Noop result. ``CancelledError`` is always re-raised so cancel semantics
are preserved (per S2-1 M9).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Auth provider returns the Bearer token string. Empty string → no
# Authorization header is sent (downstream gets an anonymous request
# rather than a malformed ``Bearer ``).
AuthProvider = Callable[[], str | Awaitable[str]]


@runtime_checkable
class ServiceClientProtocol(Protocol):
    """Structural shape required of any sibling-service client.

    Concrete adapters compose ``BaseServiceClient`` rather than inherit
    from this Protocol. The Protocol exists so type-checkers and
    runtime guards can verify shape without forcing inheritance.
    """

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response: ...

    async def safe_request(
        self,
        method: str,
        path: str,
        *,
        event: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response | None: ...

    def set_auth_provider(self, auth_provider: AuthProvider) -> None: ...


class BaseServiceClient:
    """Reference HTTP client for sibling-service integrations.

    Provides shared headers, timeout, and fail-soft request helpers.
    Adapters call ``request`` for the strict path (raises on
    network/HTTP errors) or ``safe_request`` for the degradable path
    (returns ``None`` on transient failures, logs a structured warning).
    """

    def __init__(
        self,
        *,
        base_url: str,
        auth_provider: AuthProvider,
        user_agent: str,
        timeout_s: float = 3.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_provider: AuthProvider = auth_provider
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        self._extra_headers: dict[str, str] = dict(extra_headers or {})

    # ────────── auth_provider plumbing ──────────

    def set_auth_provider(self, auth_provider: AuthProvider) -> None:
        """Replace the auth_provider closure.

        Used in Phase 0 P0.7 to swap a static-api-key closure for a
        service-JWT minter without re-creating the adapter.
        """
        self._auth_provider = auth_provider

    async def _resolve_auth_token(self) -> str:
        """Call the auth_provider, awaiting if it returned a coroutine.

        Empty/None means "no Authorization header" — caller decides what
        that means semantically.
        """
        try:
            value = self._auth_provider()
            if inspect.isawaitable(value):
                value = await value
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — auth provider failures degrade to anonymous
            logger.warning(
                "auth_provider_failed",
                base_url=self._base_url,
                error=str(exc),
            )
            return ""
        return str(value or "")

    async def _build_headers(self, override: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {"User-Agent": self._user_agent}
        headers.update(self._extra_headers)
        token = await self._resolve_auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if override:
            headers.update(override)
        return headers

    # ────────── HTTP surface ──────────

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Issue an HTTP request. Raises on httpx errors — caller handles."""
        url = self._absolute(path)
        merged_headers = await self._build_headers(headers)
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            return await client.request(method, url, headers=merged_headers, params=params, json=json)

    async def safe_request(
        self,
        method: str,
        path: str,
        *,
        event: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response | None:
        """Issue an HTTP request, swallowing transient failures.

        ``event`` is the structlog event name used when logging a
        warning so adapters keep their existing log keys.

        Returns ``None`` on:
          * ``httpx.TimeoutException``
          * ``httpx.HTTPError`` (transport, network, HTTP/1.1 framing, …)

        ``asyncio.CancelledError`` is always re-raised so cancel
        semantics are preserved (per S2-1 M9).
        """
        try:
            return await self.request(method, path, headers=headers, params=params, json=json)
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as exc:
            logger.warning(event, error=str(exc), reason="timeout", path=path)
            return None
        except httpx.HTTPError as exc:
            logger.warning(event, error=str(exc), reason="http_error", path=path)
            return None

    def _absolute(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._base_url}{path}"
