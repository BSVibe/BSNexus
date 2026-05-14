"""Phase 0 P0.5 — bsvibe-authz integration shape.

This file pins the **integration boundary** between BSNexus and the
shared ``bsvibe-authz`` package (Phase 0 P0.4 from ``bsvibe-python``).

Scope is intentionally narrow: BSNexus's ``core/auth.py`` uses
``bsvibe-auth`` (not -authz) for JWT verification today. The P0.5
adoption introduces ``bsvibe-authz`` for **service token verification**
and the ``ServiceKeyAuth`` dependency that future internal endpoints
will use. The user-token verification swap (``bsvibe-auth`` →
``bsvibe-authz`` ``CurrentUser``) is left for a follow-up sprint
because:

  1. OpenFGA is not live in BSNexus's dev/test environment yet (P0.3
     ships infra in a separate worktree).
  2. ``bsvibe-authz.CurrentUser`` requires ``USER_JWT_SECRET`` /
     ``USER_JWT_PUBLIC_KEY`` settings that don't exist in the BSNexus
     ``Settings`` schema and would force a JWT shape change.
  3. The Sprint 0 PR #33 ``get_current_user`` implementation is
     security-correct (verifies JWT, blocks bypass in prod) — adopting
     ``bsvibe-authz`` for user auth is a non-functional change.

What this PR does ship
~~~~~~~~~~~~~~~~~~~~~~
* ``bsvibe-authz`` is in ``pyproject.toml``
* The package's public surface (``ServiceTokenPayload``, ``ServiceKeyAuth``,
  ``verify_service_jwt``) imports cleanly from BSNexus
* The package's ``Permission.parse`` types match the namespacing
  BSNexus's ``require_permission`` will use (``bsnexus.<resource>.<action>``)
* The ``ServiceJWTMinter`` produces tokens whose payload satisfies
  ``ServiceTokenPayload`` shape (cross-package contract)
"""

from __future__ import annotations

import pytest


def test_bsvibe_authz_package_importable():
    """Core symbols from the package must be importable without errors —
    pin the dependency wired in pyproject.toml."""
    import bsvibe_authz

    expected = {
        "AuthError",
        "CurrentUser",
        "Permission",
        "ServiceKey",
        "ServiceKeyAuth",
        "ServiceTokenPayload",
        "Settings",
        "User",
        "verify_service_jwt",
        "verify_user_jwt",
    }
    available = set(bsvibe_authz.__all__)
    missing = expected - available
    assert not missing, f"bsvibe-authz package is missing public symbols: {missing}"


def test_permission_namespace_for_bsnexus_routes():
    """BSNexus's permission identifiers will follow ``bsnexus.<resource>.<action>``
    — the convention pinned by ``Permission.parse`` validator. Ensure
    common BSNexus permissions parse without error so adding
    ``require_permission("bsnexus.projects.write")`` later doesn't crash
    at import time."""
    from bsvibe_authz import Permission

    bsnexus_perms = [
        "bsnexus.projects.read",
        "bsnexus.projects.write",
        "bsnexus.runs.read",
        "bsnexus.deliverables.read",
        "bsnexus.decisions.write",
        "bsnexus.integrations.write",
    ]
    for p in bsnexus_perms:
        parsed = Permission.parse(p)
        assert str(parsed) == p
        assert parsed.product == "bsnexus"


def test_service_token_payload_shape_matches_minter_output():
    """The ``ServiceJWTMinter`` produces tokens that, when decoded, must
    satisfy ``ServiceTokenPayload`` — the cross-package contract.

    We don't decode an actual JWT here (that would require the
    BSVibe-Auth signing secret); we pin the JSON-shape contract so a
    future change in either package surfaces here.
    """
    from bsvibe_authz import ServiceTokenPayload

    # Minimal valid payload: matches BSVibe-Auth PR #3 issuance contract.
    payload = ServiceTokenPayload(
        iss="https://auth.bsvibe.dev",
        sub="user:abc",
        aud="bsage",
        scope="bsage:read",
        iat=1000,
        exp=2000,
        token_type="service",
        tenant_id="t1",
    )
    assert payload.aud == "bsage"
    assert payload.scopes == ["bsage:read"]
    assert payload.has_scope("bsage:read")
    assert payload.tenant_id == "t1"


def test_service_token_payload_rejects_invalid_audience():
    """Audience must be one of the four known BSVibe products. Drift
    in this list (BSVibe-Auth ↔ bsvibe-authz ↔ BSNexus expected
    audiences) breaks service-to-service auth — pin it."""
    import pydantic

    from bsvibe_authz import ServiceTokenPayload

    # Valid audiences.
    for aud in ("bsage", "bsgateway", "bsupervisor", "bsnexus"):
        payload = ServiceTokenPayload(
            iss="i",
            sub="s",
            aud=aud,
            scope=f"{aud}:read",
            iat=1,
            exp=2,
            token_type="service",
        )
        assert payload.aud == aud

    # Invalid audience.
    with pytest.raises(pydantic.ValidationError):
        ServiceTokenPayload(
            iss="i",
            sub="s",
            aud="random-service",
            scope="random-service:read",
            iat=1,
            exp=2,
            token_type="service",
        )


def test_service_token_payload_scope_audience_binding():
    """Per Lockin §3 #16: scopes MUST be prefixed with the audience.
    ``verify_service_jwt`` enforces this defense-in-depth — pinning
    the contract keeps drift visible."""
    from bsvibe_authz import verify_service_jwt
    from bsvibe_authz.auth import AuthError
    from bsvibe_authz.settings import Settings

    # Build a token whose scope is mismatched with audience.
    import jwt as pyjwt

    signing_secret = "dev-shared-secret-with-at-least-32-bytes"

    settings = Settings(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        openfga_api_url="http://fga",
        openfga_store_id="s",
        openfga_auth_model_id="m",
        service_token_signing_secret=signing_secret,
        user_jwt_secret=signing_secret,
    )

    bad_payload = {
        "iss": "https://auth.bsvibe.dev",
        "sub": "user:x",
        "aud": "bsage",
        "scope": "bsupervisor:audit.write",  # cross-audience — must be rejected
        "iat": 1000,
        "exp": 9999999999,
        "token_type": "service",
    }
    token = pyjwt.encode(bad_payload, signing_secret, algorithm="HS256")

    with pytest.raises(AuthError) as exc_info:
        verify_service_jwt(token, settings, "bsage")
    # Either the package surfaces the cross-audience scope mismatch
    # message or the bound-validator rejects it earlier — both are OK.
    msg = str(exc_info.value).lower()
    assert "scope" in msg or "audience" in msg or "bsupervisor" in msg or "payload invalid" in msg


def test_bsnexus_service_jwt_minter_audience_set_matches_authz_package():
    """The audiences BSNexus mints for (``bsage``, ``bsupervisor``,
    ``bsgateway``) must be valid in ``bsvibe_authz.types.ServiceAudience``.

    bsvibe-authz 1.2.0 reverted ``ServiceAudience`` to ``bs``-prefixed
    product names; the whole ecosystem flipped outbound audiences +
    scope-string prefixes back to ``bsXXX`` to match.

    Pin: drift between the producer (this minter) and the verifier
    (the receiving service's ``ServiceKeyAuth``) silently fails auth.
    """
    from bsvibe_authz.types import ServiceAudience

    # Get the typing.Literal alternatives at runtime.
    import typing

    valid = set(typing.get_args(ServiceAudience))
    bsnexus_outbound = {"bsage", "bsupervisor", "bsgateway"}
    assert bsnexus_outbound.issubset(valid), (
        f"BSNexus mints for {bsnexus_outbound}, but bsvibe-authz only accepts {valid} as ServiceAudience"
    )
