"""ServiceJWTMinter — Phase 0 P0.7 service-JWT auth provider.

Decision #16 (Lockin §3): service tokens are audience-scoped + scope-claim
guarded. BSVibe-Auth's ``POST /api/service-tokens/issue`` endpoint signs an
HS256 JWT with shape ``{aud, sub, scope, tenant_id, token_type:"service",
iat, exp}`` (see ``BSVibe-Auth/phase0/auth-app/api/service-tokens/issue.ts``).

The minter:

1. Calls the issuance endpoint with a bootstrap admin/owner token (the caller
   must already be admin/owner of the tenant — BSVibe-Auth enforces this).
2. Caches the returned ``access_token`` per ``(audience, tenant_id, scope)``
   tuple. The TTL is ``min(expires_in, ...) - safety_margin_s``; the safety
   margin (default 60s) ensures we never serve a token that's a few seconds
   from expiring and can't survive a downstream retry.
3. Coalesces concurrent ``mint`` calls for the same tuple through a per-key
   ``asyncio.Lock`` so a thundering herd on cold start doesn't fan out into
   N parallel POSTs to the auth server.

Fail-soft contract
------------------
``mint`` NEVER raises. Upstream errors return ``""`` so the
``BaseServiceClient`` sends an anonymous request — the receiving service
returns a clean 401 (which our adapters degrade to Noop) instead of a
cascading 500 inside BSNexus.

P0.7 swap (Decision #15 contract)
---------------------------------
``make_auth_provider(audience=..., tenant_id=..., scope=...)`` returns a
callable matching ``BaseServiceClient.AuthProvider``. Adapter code does
NOT change — only the closure passed to ``BaseServiceClient`` at
construction (or via ``set_auth_provider`` for live swap).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


# Endpoint exposed by BSVibe-Auth (PR #3, Phase 0 P0.7).
_ISSUE_PATH = "/api/service-tokens/issue"

# How long to wait for the auth server to mint a token. The default httpx
# timeout would let a misbehaving auth server stall the entire run.
_MINT_TIMEOUT_S = 5.0

# Bootstrap token provider — sync or async callable that returns the caller's
# admin/owner Bearer token. Typically wraps a stored long-lived service-account
# credential (P0.7 §sandbox creds) or the founder's own session token.
BootstrapTokenProvider = Callable[[], str | Awaitable[str]]


@dataclass
class _CachedToken:
    """A minted token + the ``time.monotonic()`` clock past which it
    should be re-minted (already accounting for the safety margin)."""

    token: str
    expires_at: float


class ServiceJWTMinter:
    """Mints + caches service-to-service JWTs from BSVibe-Auth.

    Thread/coroutine safety:
      * The cache is process-local. Per-key ``asyncio.Lock`` coalesces
        concurrent mints for the same key.
      * The bootstrap token is read once per ``mint`` call; rotating it
        via ``BootstrapTokenProvider`` is supported.
    """

    def __init__(
        self,
        *,
        bsvibe_auth_url: str,
        bootstrap_token_provider: BootstrapTokenProvider,
        timeout_s: float = _MINT_TIMEOUT_S,
        safety_margin_s: int = 60,
    ) -> None:
        self._auth_url = bsvibe_auth_url.rstrip("/")
        self._bootstrap_provider = bootstrap_token_provider
        self._timeout_s = timeout_s
        self._safety_margin_s = safety_margin_s
        self._cache: dict[tuple[str, str, str], _CachedToken] = {}
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    def _key(self, audience: str, tenant_id: str, scope: list[str]) -> tuple[str, str, str]:
        # Normalise scope ordering so callers can pass scope in any order
        # without missing the cache hit. Lock-in §3 #16 sorts scopes
        # alphabetically for the canonical signing payload anyway.
        return (audience, tenant_id, ",".join(sorted(scope)))

    def _get_lock(self, key: tuple[str, str, str]) -> asyncio.Lock:
        # Lazy-create per-key locks. Same pattern as
        # ``integrations/config.py:_get_tenant_lock`` (S1-3 H12 — single
        # asyncio loop guarantees the dict.get / dict[...] = pair is
        # not preempted).
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _resolve_bootstrap_token(self) -> str:
        try:
            value = self._bootstrap_provider()
            if asyncio.iscoroutine(value):
                value = await value
        except Exception as exc:  # noqa: BLE001 — fail-soft on bootstrap provider failure
            logger.warning("service_jwt_bootstrap_provider_failed", error=str(exc))
            return ""
        return str(value or "")

    async def mint(
        self,
        *,
        audience: str,
        tenant_id: str,
        scope: list[str],
    ) -> str:
        """Return a service JWT for ``(audience, tenant_id, scope)``.

        Cached for the token's TTL minus the safety margin. Returns an
        empty string on any upstream failure (logs a warning) so the
        caller's auth_provider closure produces an anonymous request
        rather than a cascading 500 inside BSNexus.
        """
        key = self._key(audience, tenant_id, scope)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached.expires_at > now:
            return cached.token

        lock = self._get_lock(key)
        async with lock:
            # Re-check under the lock — a peer coroutine may have
            # populated the cache while we were waiting.
            cached = self._cache.get(key)
            now = time.monotonic()
            if cached is not None and cached.expires_at > now:
                return cached.token

            token, ttl_s = await self._issue(audience=audience, tenant_id=tenant_id, scope=scope)
            if not token:
                # Upstream failed — don't cache so the next call retries.
                return ""

            # safety margin: a token with ttl 600s, margin 60s caches for
            # 540s. With ttl 1s and margin 60s, the cache "expires before
            # it stored" — that's intentional, see test_refreshes_after_expiry.
            self._cache[key] = _CachedToken(
                token=token,
                expires_at=time.monotonic() + max(ttl_s - self._safety_margin_s, 0),
            )
            return token

    async def _issue(
        self,
        *,
        audience: str,
        tenant_id: str,
        scope: list[str],
    ) -> tuple[str, int]:
        """POST to ``/api/service-tokens/issue``. Returns ``(token, ttl_s)``
        on success or ``("", 0)`` on any failure.
        """
        bootstrap = await self._resolve_bootstrap_token()
        if not bootstrap:
            logger.warning(
                "service_jwt_no_bootstrap",
                audience=audience,
                tenant_id=tenant_id,
            )
            return "", 0

        url = f"{self._auth_url}{_ISSUE_PATH}"
        headers = {
            "Authorization": f"Bearer {bootstrap}",
            "User-Agent": "BSNexus/0.2 (+https://nexus.bsvibe.dev)",
        }
        body = {
            "audience": audience,
            "scope": list(scope),
            "tenant_id": tenant_id,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                payload: dict[str, Any] = resp.json() or {}
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "service_jwt_mint_http_error",
                audience=audience,
                tenant_id=tenant_id,
                status=exc.response.status_code if exc.response is not None else 0,
                error=str(exc),
            )
            return "", 0
        except httpx.HTTPError as exc:
            logger.warning(
                "service_jwt_mint_transport_error",
                audience=audience,
                tenant_id=tenant_id,
                error=str(exc),
            )
            return "", 0
        except Exception as exc:  # noqa: BLE001 — fail-soft on parse / unknown
            logger.warning(
                "service_jwt_mint_unknown_error",
                audience=audience,
                tenant_id=tenant_id,
                error=str(exc),
            )
            return "", 0

        token = str(payload.get("access_token") or "")
        try:
            ttl_s = int(payload.get("expires_in") or 0)
        except (TypeError, ValueError):
            ttl_s = 0
        if not token or ttl_s <= 0:
            logger.warning(
                "service_jwt_mint_malformed_response",
                audience=audience,
                tenant_id=tenant_id,
                has_token=bool(token),
                ttl_s=ttl_s,
            )
            return "", 0
        return token, ttl_s

    def make_auth_provider(
        self,
        *,
        audience: str,
        tenant_id: str,
        scope: list[str],
    ) -> Callable[[], Awaitable[str]]:
        """Return a closure suitable for ``BaseServiceClient.auth_provider``.

        This is the P0.7 swap point: replace the static-api-key closure
        passed to ``BaseServiceClient`` with the result of this method
        and the same adapter starts authenticating with service JWTs —
        no adapter code changes (Decision #15 contract).
        """

        async def _provider() -> str:
            return await self.mint(audience=audience, tenant_id=tenant_id, scope=scope)

        return _provider

    def invalidate(self, *, audience: str, tenant_id: str, scope: list[str]) -> None:
        """Drop a cached entry — used when a downstream service rejects
        the token (e.g. signing key rotated). Tests/operators only;
        the steady-state path relies on TTL.
        """
        self._cache.pop(self._key(audience, tenant_id, scope), None)


_global_minter: ServiceJWTMinter | None = None


def get_service_jwt_minter() -> ServiceJWTMinter | None:
    """Return the process-wide ServiceJWTMinter, or ``None`` when the
    service-account credential is not configured.

    BSGateway/BSage/BSupervisor-bound calls (run dispatch + audit) thread
    this through ``resolve_knowledge_client`` /``resolve_audit_sink`` so
    the BaseServiceClient ``auth_provider`` closure mints service JWTs
    instead of consulting the legacy static api_key. With None, the
    factories fall back to the api_key path (Phase A drop) so dev
    environments without a configured service account keep working.
    """
    global _global_minter

    # Late import — config must already be loaded by the time anyone
    # calls this. Avoiding the import at module load lets tests inject
    # their own minter via ``set_service_jwt_minter``.
    from backend.src.config import settings  # noqa: PLC0415

    if not settings.bsnexus_service_account_token:
        return None
    if _global_minter is None:
        token = settings.bsnexus_service_account_token

        def _bootstrap() -> str:
            # Re-read settings on each mint — supports rotation without
            # restart (write the new token, mint cache TTL expires,
            # next mint pulls the rotated value).
            from backend.src.config import settings as _s  # noqa: PLC0415

            return _s.bsnexus_service_account_token or token

        _global_minter = ServiceJWTMinter(
            bsvibe_auth_url=settings.bsvibe_auth_url,
            bootstrap_token_provider=_bootstrap,
        )
    return _global_minter


def set_service_jwt_minter(minter: ServiceJWTMinter | None) -> None:
    """Override the process-wide minter — used by tests."""
    global _global_minter
    _global_minter = minter
