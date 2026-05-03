"""S1-3 — startup security guards.

Hardens the lifespan startup checks introduced in S0-3:

* H9 (BSNexus / Audit §5): ``prompt_signing_key`` and ``encryption_key``
  defaults must be rejected when ``ENVIRONMENT=production`` regardless
  of ``debug`` flag. ``debug=False`` alone is *not* a strong enough
  signal — a deploy could leak the dev keys with ``ENVIRONMENT=production``
  + ``debug=False`` defaulting unset, but also a dev image with
  ``debug=False`` should not silently fall through to production
  enforcement and crash. The explicit production switch makes the
  contract one-way: production REJECTS dev defaults, period.

* M19 (BSNexus / Audit §5): ``frontend_url`` default ``localhost:3000``
  must be rejected in production. The cookie-SSO redirect/login flow
  needs the real production host or login bounces back to localhost.

These guards are pure synchronous validators — they take a ``Settings``
instance and return ``None`` (ok) or raise ``RuntimeError``. The lifespan
function delegates to them so we can unit-test the policy without
spinning up the full app.
"""

from __future__ import annotations

import pytest

from backend.src.config import Settings
from backend.src.core.startup_guards import (
    DevDefaultLeakError,
    enforce_production_security_guards,
)


def _make_settings(**overrides) -> Settings:
    """Build a Settings instance bypassing env-file loading."""
    base = {
        "redis_url": "redis://localhost",
        "database_url": "postgresql+asyncpg://x@localhost/x",
        "bsvibe_auth_url": "https://auth.bsvibe.dev",
        "frontend_url": "https://nexus.bsvibe.dev",
        "prompt_signing_key": "real-signing-key",
        "encryption_key": "real-encryption-key-32-bytes-long-aaa",
        "mcp_signing_key": "real-mcp-signing-key-32-bytes-long",
        "mcp_internal_url": "https://nexus-internal.bsvibe.dev",
        "environment": "production",
    }
    base.update(overrides)
    return Settings(**base)


# ── H9: prompt_signing_key dev default ─────────────────────────────────


def test_production_rejects_default_prompt_signing_key():
    settings = _make_settings(prompt_signing_key="dev-signing-key-change-in-production")
    with pytest.raises(DevDefaultLeakError) as exc_info:
        enforce_production_security_guards(settings)
    assert "prompt_signing_key" in str(exc_info.value).lower()


def test_production_accepts_real_prompt_signing_key():
    settings = _make_settings(prompt_signing_key="real-32-bytes-of-entropy-here-yes")
    enforce_production_security_guards(settings)


# ── H9: encryption_key dev default ─────────────────────────────────────


def test_production_rejects_default_encryption_key():
    settings = _make_settings(encryption_key="dev-encryption-key-change-in-production")
    with pytest.raises(DevDefaultLeakError) as exc_info:
        enforce_production_security_guards(settings)
    assert "encryption_key" in str(exc_info.value).lower()


# ── M19: frontend_url localhost default ────────────────────────────────


def test_production_rejects_default_frontend_url_localhost():
    settings = _make_settings(frontend_url="http://localhost:3000")
    with pytest.raises(DevDefaultLeakError) as exc_info:
        enforce_production_security_guards(settings)
    assert "frontend_url" in str(exc_info.value).lower()


def test_production_rejects_any_loopback_frontend_url():
    """Any 127.0.0.1 / 0.0.0.0 / localhost host must be rejected — those
    are dev/test addresses and would break SSO redirects."""
    for url in [
        "http://127.0.0.1:3000",
        "http://0.0.0.0:3000",
        "http://localhost",
        "https://localhost:8443",
    ]:
        settings = _make_settings(frontend_url=url)
        with pytest.raises(DevDefaultLeakError):
            enforce_production_security_guards(settings)


def test_production_accepts_real_frontend_url():
    settings = _make_settings(frontend_url="https://nexus.bsvibe.dev")
    enforce_production_security_guards(settings)


# ── Direction reset 2026-05-03: MCP signing key + internal URL ───────


def test_production_rejects_default_mcp_signing_key():
    """Compromise of ``BSNEXUS_MCP_SIGNING_KEY`` lets any actor on
    ``/mcp/sse`` mint forged run-scoped tokens — startup must refuse to
    boot if the dev default leaks into production."""
    settings = _make_settings(mcp_signing_key="dev-mcp-signing-key-change-in-production")
    with pytest.raises(DevDefaultLeakError) as exc_info:
        enforce_production_security_guards(settings)
    assert "mcp_signing_key" in str(exc_info.value).lower()


def test_production_rejects_default_mcp_internal_url():
    """``mcp_internal_url`` is the URL embedded in
    ``metadata.mcp_servers["bsnexus"].url`` for BSGateway workers to
    call back. Loopback / dev default would mean every MCP tool call
    silently fails because workers can't resolve ``localhost``."""
    settings = _make_settings(mcp_internal_url="http://localhost:18100")
    with pytest.raises(DevDefaultLeakError) as exc_info:
        enforce_production_security_guards(settings)
    assert "mcp_internal_url" in str(exc_info.value).lower()


def test_production_rejects_any_loopback_mcp_internal_url():
    for url in [
        "http://127.0.0.1:18100",
        "http://0.0.0.0:18100",
        "https://localhost:18100",
    ]:
        settings = _make_settings(mcp_internal_url=url)
        with pytest.raises(DevDefaultLeakError):
            enforce_production_security_guards(settings)


# ── Non-production behavior ────────────────────────────────────────────


def test_non_production_environment_skips_guards():
    """Dev/test/staging environments may use the dev defaults — guards
    only fire when ``environment=production``."""
    for env in ["", "development", "dev", "staging", "test"]:
        settings = _make_settings(
            environment=env,
            prompt_signing_key="dev-signing-key-change-in-production",
            encryption_key="dev-encryption-key-change-in-production",
            frontend_url="http://localhost:3000",
        )
        # No exception expected.
        enforce_production_security_guards(settings)


def test_production_environment_case_insensitive():
    """``ENVIRONMENT=PRODUCTION`` and ``Production`` must both fire."""
    for env in ["production", "PRODUCTION", "Production", "  production  "]:
        settings = _make_settings(
            environment=env,
            prompt_signing_key="dev-signing-key-change-in-production",
        )
        with pytest.raises(DevDefaultLeakError):
            enforce_production_security_guards(settings)


def test_dev_default_leak_error_is_runtime_error():
    """The custom exception must subclass ``RuntimeError`` so the lifespan
    crash behavior matches the prior contract — ASGI servers stop on
    RuntimeError at lifespan startup."""
    assert issubclass(DevDefaultLeakError, RuntimeError)
