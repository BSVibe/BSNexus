"""Run-scoped HMAC tokens for the BSNexus MCP server.

Lifecycle:

1. Dispatcher mints a token just before the BSGateway chat completion
   (``issue_run_scoped_token``) with a TTL = run timeout + 5 min grace.
2. The dispatcher embeds the token in the BSGateway request as
   ``metadata.mcp_servers["bsnexus"].url = ".../mcp/sse?token=<token>"``.
3. BSGateway forwards through to the worker; the worker spawns claude
   CLI with ``--mcp-config`` pointing at that URL.
4. Claude opens the SSE connection; the BSNexus SSE handler calls
   ``verify_run_scoped_token`` on the query param before allowing any
   tool dispatch.

Format: a compact JWT-like ``header.payload.signature`` triple. We
use a custom HMAC-SHA256 scheme rather than a third-party JWT lib to
keep the dependency surface tiny — the claim shape is fixed and
verifier is ~30 lines.

The signing key lives in :func:`backend.src.config.settings`
(``BSNEXUS_MCP_SIGNING_KEY`` env var). Rotation requires invalidating
all in-flight runs (re-dispatch). Compromise of the key impersonates
*any* run — treat as a top-tier secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

REQUIRED_CLAIM_KEYS = ("run_id", "tenant_id", "project_id")
_HEADER = {"alg": "HS256", "typ": "MCP1"}


class MCPAuthError(Exception):
    """Raised on any auth failure. Surface as 401 from the SSE handler."""


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(key: str, msg: str) -> str:
    digest = hmac.new(key.encode("utf-8"), msg.encode("ascii"), hashlib.sha256).digest()
    return _b64u_encode(digest)


def issue_run_scoped_token(
    claim: dict[str, Any],
    *,
    signing_key: str,
    ttl_seconds: int,
) -> str:
    """Mint a token. ``claim`` must include run_id / tenant_id / project_id.

    ``ttl_seconds`` may be negative — verification will reject as expired,
    handy for testing the rejection path.
    """
    now = int(time.time())
    payload = dict(claim)
    payload["iat"] = now
    payload["exp"] = now + ttl_seconds

    header_b64 = _b64u_encode(json.dumps(_HEADER, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64u_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}"
    signature = _sign(signing_key, signing_input)
    return f"{signing_input}.{signature}"


def verify_run_scoped_token(
    token: str,
    *,
    signing_key: str,
    assert_run_id: str | None = None,
    assert_tenant_id: str | None = None,
) -> dict[str, Any]:
    """Verify and return the claim payload. Raises ``MCPAuthError`` on any
    failure — bad shape, bad signature, expired, missing required keys,
    cross-run mismatch.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise MCPAuthError("malformed token (expected three dot-separated segments)")
    header_b64, payload_b64, signature_b64 = parts

    expected_sig = _sign(signing_key, f"{header_b64}.{payload_b64}")
    if not hmac.compare_digest(expected_sig, signature_b64):
        raise MCPAuthError("bad signature")

    try:
        payload_raw = _b64u_decode(payload_b64)
        payload = json.loads(payload_raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MCPAuthError(f"unparseable payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise MCPAuthError("payload must be a JSON object")

    for key in REQUIRED_CLAIM_KEYS:
        if key not in payload:
            raise MCPAuthError(f"missing required claim: {key}")

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise MCPAuthError("token expired")

    if assert_run_id is not None and payload["run_id"] != assert_run_id:
        raise MCPAuthError("run_id mismatch")
    if assert_tenant_id is not None and payload["tenant_id"] != assert_tenant_id:
        raise MCPAuthError("tenant_id mismatch")

    return payload
