"""Tests for ``core.mcp.auth`` — run-scoped HMAC tokens.

Tokens are minted by the dispatcher just before a BSGateway chat
completion, embedded in ``metadata.mcp_servers["bsnexus"].url`` as a
``?token=`` query param, and verified on every MCP `/mcp/http` request.
The token claim is ``{run_id, tenant_id, project_id, iat, exp}`` —
verification rejects cross-tenant or run-mismatched tokens so a
compromised run can never access another run's decisions or artifacts.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.mcp.auth import (
    MCPAuthError,
    issue_run_scoped_token,
    verify_run_scoped_token,
)


SIGNING_KEY = "test-signing-key-32-bytes-long-x"


def _claim() -> dict:
    return {
        "run_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
    }


def test_issue_then_verify_roundtrips() -> None:
    claim = _claim()
    token = issue_run_scoped_token(claim, signing_key=SIGNING_KEY, ttl_seconds=300)

    decoded = verify_run_scoped_token(token, signing_key=SIGNING_KEY)

    assert decoded["run_id"] == claim["run_id"]
    assert decoded["tenant_id"] == claim["tenant_id"]
    assert decoded["project_id"] == claim["project_id"]
    assert decoded["iat"] > 0
    assert decoded["exp"] > decoded["iat"]


def test_verify_with_wrong_key_rejects() -> None:
    token = issue_run_scoped_token(_claim(), signing_key=SIGNING_KEY, ttl_seconds=300)

    with pytest.raises(MCPAuthError, match="signature"):
        verify_run_scoped_token(token, signing_key="other-key-also-32-bytes-long-aa")


def test_verify_after_expiry_rejects() -> None:
    token = issue_run_scoped_token(_claim(), signing_key=SIGNING_KEY, ttl_seconds=-10)

    with pytest.raises(MCPAuthError, match="expired"):
        verify_run_scoped_token(token, signing_key=SIGNING_KEY)


def test_verify_tampered_payload_rejects() -> None:
    token = issue_run_scoped_token(_claim(), signing_key=SIGNING_KEY, ttl_seconds=300)
    parts = token.split(".")
    # flip a byte in the middle of the payload
    bad = parts[0] + "." + parts[1][:-1] + ("a" if parts[1][-1] != "a" else "b") + "." + parts[2]

    with pytest.raises(MCPAuthError):
        verify_run_scoped_token(bad, signing_key=SIGNING_KEY)


def test_verify_required_claim_present() -> None:
    """Token claim must include the three scoping ids."""
    token = issue_run_scoped_token(
        {"run_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4())},  # missing project_id
        signing_key=SIGNING_KEY,
        ttl_seconds=300,
    )

    with pytest.raises(MCPAuthError, match="project_id"):
        verify_run_scoped_token(token, signing_key=SIGNING_KEY)


def test_verify_assert_run_id() -> None:
    """``assert_run_id`` lets the SSE handler enforce the token belongs to
    *this* run before dispatching tools — defence in depth in case the
    URL got cached/reused across runs."""
    claim = _claim()
    token = issue_run_scoped_token(claim, signing_key=SIGNING_KEY, ttl_seconds=300)

    decoded = verify_run_scoped_token(
        token, signing_key=SIGNING_KEY, assert_run_id=claim["run_id"]
    )
    assert decoded["run_id"] == claim["run_id"]

    with pytest.raises(MCPAuthError, match="run_id"):
        verify_run_scoped_token(
            token, signing_key=SIGNING_KEY, assert_run_id=str(uuid.uuid4())
        )


def test_token_is_url_safe_base64() -> None:
    """Token must be safe to embed in a URL query string without escaping."""
    token = issue_run_scoped_token(_claim(), signing_key=SIGNING_KEY, ttl_seconds=300)
    # No whitespace, no '+', '/', '=' (base64url replaces those)
    assert " " not in token
    assert "+" not in token
    assert "/" not in token
    assert "=" not in token
