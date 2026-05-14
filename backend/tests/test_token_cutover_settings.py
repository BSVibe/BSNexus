"""Settings must expose the RFC 7662 introspection fields.

bsvibe-authz dispatch (opaque RFC 7662 introspection / JWT) needs three
settings so operators can configure the introspection upstream without
patching code.
"""

from __future__ import annotations

import pytest


def test_settings_expose_introspection_fields() -> None:
    """The three fields must exist with empty-string defaults."""
    from backend.src.config import Settings

    s = Settings()
    assert hasattr(s, "introspection_url")
    assert hasattr(s, "introspection_client_id")
    assert hasattr(s, "introspection_client_secret")

    assert s.introspection_url == ""
    assert s.introspection_client_id == ""
    assert s.introspection_client_secret == ""


def test_settings_load_introspection_fields_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env vars must override the empty defaults."""
    monkeypatch.setenv(
        "INTROSPECTION_URL",
        "https://auth.bsvibe.dev/oauth/introspect",
    )
    monkeypatch.setenv("INTROSPECTION_CLIENT_ID", "bsnexus-introspect")
    monkeypatch.setenv("INTROSPECTION_CLIENT_SECRET", "secret-shh")

    from backend.src.config import Settings

    s = Settings()
    assert s.introspection_url == "https://auth.bsvibe.dev/oauth/introspect"
    assert s.introspection_client_id == "bsnexus-introspect"
    assert s.introspection_client_secret == "secret-shh"


def test_settings_introspection_fields_are_typed_str() -> None:
    """All three fields must be ``str`` for env-loading round-trip safety."""
    from backend.src.config import Settings

    fields = Settings.model_fields
    for name in (
        "introspection_url",
        "introspection_client_id",
        "introspection_client_secret",
    ):
        assert fields[name].annotation is str, f"{name} must be typed str"
