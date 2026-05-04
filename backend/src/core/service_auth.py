"""ServiceJWTMinter — OAuth2 client_credentials wrapper.

BSNexus-flavoured fail-soft wrapper around
:class:`bsvibe_authz.ServiceTokenMinter`. The shared library mints one
JWT per ``(audience, scope)`` pair via BSVibe-Auth's
``POST /api/oauth/token`` (RFC 6749 §4.4). This file preserves the
``mint(audience, tenant_id, scope)`` / ``make_auth_provider`` /
``invalidate`` surface that BSNexus callers (composer/knowledge_client +
audit/audit_sink) wired to via ``BaseServiceClient.AuthProvider``, and
adds the existing fail-soft contract so adapter code keeps degrading to
Noop on any auth-server failure rather than raising mid-run.

Cutover note (handoff §10): the previous implementation hit
``/api/service-tokens/issue`` with a long-lived Supabase admin
access_token rotated by a launchd timer. The new path is a per-backend
``client_id`` + ``client_secret`` provisioned in ``oauth_clients`` —
no rotation timer, no admin user dependency. The cache key still
includes ``tenant_id`` for log/parity reasons but routing-wise the
``oauth_clients`` row owns it: each minted JWT carries
``tenant_id = oauth_clients.tenant_id`` (BSNexus admin tenant).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog
from bsvibe_authz import ServiceTokenMinter as _BSVibeAuthzMinter
from bsvibe_authz import ServiceTokenMinterError

logger = structlog.get_logger(__name__)


# Cache key shape kept compatible with the previous implementation —
# `(audience, tenant_id, sorted-comma-joined-scope)`. tenant_id no
# longer steers the OAuth2 mint (the row carries it) but we keep it in
# the key so log lines + per-tenant metrics stay distinguishable when
# the same process serves multiple tenants in dev/test.
_CacheKey = tuple[str, str, str]


class ServiceJWTMinter:
    """Mints + caches service-to-service JWTs via OAuth2 client_credentials.

    ``mint`` NEVER raises. Auth-server failures resolve to ``""`` so
    ``BaseServiceClient`` sends an anonymous request — the receiving
    service returns a clean 401, which adapters degrade to Noop.
    """

    def __init__(
        self,
        *,
        bsvibe_auth_url: str,
        client_id: str,
        client_secret: str,
        timeout_s: float = 5.0,
        safety_margin_s: int = 60,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret must be non-empty")
        self._auth_url = bsvibe_auth_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout_s = timeout_s
        self._safety_margin_s = safety_margin_s
        self._minters: dict[_CacheKey, _BSVibeAuthzMinter] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(audience: str, tenant_id: str, scope: list[str]) -> _CacheKey:
        return (audience, tenant_id, ",".join(sorted(scope)))

    async def _get_or_create_minter(self, audience: str, tenant_id: str, scope: list[str]) -> _BSVibeAuthzMinter:
        key = self._key(audience, tenant_id, scope)
        existing = self._minters.get(key)
        if existing is not None:
            return existing
        async with self._lock:
            existing = self._minters.get(key)
            if existing is not None:
                return existing
            minter = _BSVibeAuthzMinter(
                auth_url=self._auth_url,
                client_id=self._client_id,
                client_secret=self._client_secret,
                audience=audience,
                scope=scope,
                timeout_s=self._timeout_s,
                safety_margin_s=self._safety_margin_s,
            )
            self._minters[key] = minter
            return minter

    async def mint(
        self,
        *,
        audience: str,
        tenant_id: str,
        scope: list[str],
    ) -> str:
        """Return a service JWT for ``(audience, scope)``.

        ``tenant_id`` is propagated to log fields but does NOT influence
        the underlying OAuth2 mint — the ``oauth_clients`` row owns it.
        Returns ``""`` on any failure (fail-soft contract).
        """
        try:
            minter = await self._get_or_create_minter(audience, tenant_id, scope)
            return await minter.get_token()
        except asyncio.CancelledError:
            raise
        except ServiceTokenMinterError as exc:
            logger.warning(
                "service_jwt_mint_oauth_error",
                audience=audience,
                tenant_id=tenant_id,
                error=str(exc),
            )
            return ""
        except ValueError as exc:
            # Validation at construction (bad audience/scope) — config bug.
            logger.warning(
                "service_jwt_mint_config_error",
                audience=audience,
                tenant_id=tenant_id,
                error=str(exc),
            )
            return ""
        except Exception as exc:  # noqa: BLE001 — fail-soft on parse / unknown
            logger.warning(
                "service_jwt_mint_unknown_error",
                audience=audience,
                tenant_id=tenant_id,
                error=str(exc),
            )
            return ""

    def make_auth_provider(
        self,
        *,
        audience: str,
        tenant_id: str,
        scope: list[str],
    ) -> Callable[[], Awaitable[str]]:
        """Return a ``BaseServiceClient.AuthProvider``-compatible closure.

        P0.7 swap point (Decision #15): adapters keep their
        ``BaseServiceClient`` constructor signature; only the closure
        changes.
        """

        async def _provider() -> str:
            return await self.mint(
                audience=audience,
                tenant_id=tenant_id,
                scope=list(scope),
            )

        return _provider

    def invalidate(self, *, audience: str, tenant_id: str, scope: list[str]) -> None:
        """Drop a cached entry — used when a downstream service rejects
        the token (e.g. signing key rotated).
        """
        key = self._key(audience, tenant_id, scope)
        minter = self._minters.pop(key, None)
        if minter is not None:
            minter.invalidate()


_global_minter: ServiceJWTMinter | None = None


def get_service_jwt_minter() -> ServiceJWTMinter | None:
    """Return the process-wide ``ServiceJWTMinter``, or ``None`` when the
    OAuth2 client credentials are not configured.

    With ``None``, ``resolve_knowledge_client`` / ``resolve_audit_sink``
    drop to Noop adapters so dev environments without a configured
    OAuth client keep working.
    """
    global _global_minter

    # Late import — config must already be loaded. Avoiding the import
    # at module load lets tests inject their own minter via
    # ``set_service_jwt_minter``.
    from backend.src.config import settings  # noqa: PLC0415

    if not settings.bsvibe_client_id or not settings.bsvibe_client_secret:
        return None
    if _global_minter is None:
        _global_minter = ServiceJWTMinter(
            bsvibe_auth_url=settings.bsvibe_auth_url,
            client_id=settings.bsvibe_client_id,
            client_secret=settings.bsvibe_client_secret,
        )
    return _global_minter


def set_service_jwt_minter(minter: ServiceJWTMinter | None) -> None:
    """Override the process-wide minter — used by tests."""
    global _global_minter
    _global_minter = minter
