"""Production startup security guards (S1-3).

These guards run at lifespan startup and crash the process if any
``settings`` value still carries a development default in a production
deployment. The contract is intentionally one-way:

  * ``ENVIRONMENT=production`` → guards FIRE. Dev defaults are
    rejected with a clear ``DevDefaultLeakError``.
  * Anything else (unset, ``development``, ``staging``, ``test``) →
    guards skip. Local dev / Playwright CI keeps working with the
    convenient defaults.

This is more deterministic than the prior ``not debug`` check: a
deployment can have ``debug=False`` but still be a non-prod environment
(staging probe), and we don't want staging to crash on dev defaults.
The explicit ``environment=production`` opt-in is the *only* signal
that triggers enforcement.

Audit references:

* H9 — ``prompt_signing_key`` / ``encryption_key`` defaults
  (``BSVibe_Ecosystem_Audit.md §5.2``).
* M19 — ``frontend_url`` default ``localhost:3000`` (§5.3).
"""

from __future__ import annotations

from urllib.parse import urlparse

from backend.src.config import Settings

_DEV_SIGNING_KEY = Settings.model_fields["prompt_signing_key"].default
_DEV_ENCRYPTION_KEY = Settings.model_fields["encryption_key"].default
_DEV_FRONTEND_URL = Settings.model_fields["frontend_url"].default
# Direction reset 2026-05-03 — MCP server signing key. Compromise lets
# any actor on the public ``/mcp/sse`` path mint forged run-scoped
# tokens for any tenant/run/project (claim contains all three). Treat
# as top-tier secret per ``core/mcp/auth.py`` docstring.
_DEV_MCP_SIGNING_KEY = Settings.model_fields["mcp_signing_key"].default
_DEV_MCP_INTERNAL_URL = Settings.model_fields["mcp_internal_url"].default

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class DevDefaultLeakError(RuntimeError):
    """Raised at startup when a dev default leaks into production.

    Subclasses ``RuntimeError`` so the existing ASGI lifespan crash
    contract — and any operator handler watching for RuntimeError —
    keeps working unchanged.
    """


def _is_production(settings: Settings) -> bool:
    return (settings.environment or "").strip().lower() == "production"


def _is_loopback_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return host in _LOOPBACK_HOSTS


def enforce_production_security_guards(settings: Settings) -> None:
    """Validate that dev defaults are not leaking into production.

    Returns ``None`` on success; raises :class:`DevDefaultLeakError`
    listing every offending field on failure so operators can fix all
    of them in one redeploy rather than playing whack-a-mole.
    """
    if not _is_production(settings):
        return

    offenders: list[str] = []

    if settings.prompt_signing_key == _DEV_SIGNING_KEY:
        offenders.append("prompt_signing_key — set a secure PROMPT_SIGNING_KEY env var")
    if settings.encryption_key == _DEV_ENCRYPTION_KEY:
        offenders.append("encryption_key — set a secure ENCRYPTION_KEY env var (>=32 bytes)")
    if settings.frontend_url == _DEV_FRONTEND_URL or _is_loopback_url(settings.frontend_url):
        offenders.append(
            f"frontend_url — current value {settings.frontend_url!r} is a "
            "loopback / dev default; set FRONTEND_URL to the public origin "
            "(e.g. https://nexus.bsvibe.dev)"
        )
    if settings.mcp_signing_key == _DEV_MCP_SIGNING_KEY:
        offenders.append(
            "mcp_signing_key — set a secure BSNEXUS_MCP_SIGNING_KEY env var "
            "(>=32 bytes). Compromise lets any client on the /mcp/sse path "
            "mint forged run-scoped tokens for any tenant/run."
        )
    if (
        settings.mcp_internal_url == _DEV_MCP_INTERNAL_URL
        or _is_loopback_url(settings.mcp_internal_url)
    ):
        offenders.append(
            f"mcp_internal_url — current value {settings.mcp_internal_url!r} "
            "is a loopback / dev default; set BSNEXUS_MCP_INTERNAL_URL to the "
            "BSGateway-reachable internal URL (e.g. https://nexus-internal.bsvibe.dev)"
        )

    if offenders:
        msg = (
            "FATAL: production deployment is using development defaults. "
            "Refusing to start. Offending settings:\n  - " + "\n  - ".join(offenders)
        )
        raise DevDefaultLeakError(msg)
