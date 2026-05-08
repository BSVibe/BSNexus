"""TASK-002: Settings must expose bootstrap_token + introspection fields.

Phase 1 of BSVibe AI-Native Control Plane — token cutover. The hybrid
3-way auth dispatch (bootstrap_token / opaque RFC 7662 introspection /
JWT) needs four new settings so operators can configure the introspection
upstream and the SHA-256 hash of the bootstrap admin token without
patching code.

Reference: ~/Docs/BSVibe_Phase1_Decisions_2026-05-07.md.
"""

from __future__ import annotations

import pytest


def test_settings_expose_token_cutover_fields() -> None:
    """The four new fields must exist with empty-string defaults."""
    from backend.src.config import Settings

    s = Settings()
    assert hasattr(s, "bootstrap_token_hash")
    assert hasattr(s, "introspection_url")
    assert hasattr(s, "introspection_client_id")
    assert hasattr(s, "introspection_client_secret")

    assert s.bootstrap_token_hash == ""
    assert s.introspection_url == ""
    assert s.introspection_client_id == ""
    assert s.introspection_client_secret == ""


def test_settings_load_token_cutover_fields_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env vars must override the empty defaults."""
    monkeypatch.setenv(
        "BOOTSTRAP_TOKEN_HASH",
        "a" * 64,
    )
    monkeypatch.setenv(
        "INTROSPECTION_URL",
        "https://auth.bsvibe.dev/oauth/introspect",
    )
    monkeypatch.setenv("INTROSPECTION_CLIENT_ID", "bsnexus-introspect")
    monkeypatch.setenv("INTROSPECTION_CLIENT_SECRET", "secret-shh")

    from backend.src.config import Settings

    s = Settings()
    assert s.bootstrap_token_hash == "a" * 64
    assert s.introspection_url == "https://auth.bsvibe.dev/oauth/introspect"
    assert s.introspection_client_id == "bsnexus-introspect"
    assert s.introspection_client_secret == "secret-shh"


def test_settings_token_cutover_fields_are_typed_str() -> None:
    """All four fields must be ``str`` for env-loading round-trip safety."""
    from backend.src.config import Settings

    fields = Settings.model_fields
    for name in (
        "bootstrap_token_hash",
        "introspection_url",
        "introspection_client_id",
        "introspection_client_secret",
    ):
        assert fields[name].annotation is str, f"{name} must be typed str"
