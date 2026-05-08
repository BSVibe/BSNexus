"""Parse a fenced ``bsnexus-verification`` block out of an LLM chat reply.

The worker-shared-policy prompt instructs the worker to end its reply
with a fenced JSON block describing how the server-side Verifier Worker
should re-run the same verification independently:

.. code-block:: text

    ```bsnexus-verification
    {
      "verifier_type": "software_test",
      "command": ["pytest", "-q", "tests/"],
      "cwd": "tests/",
      "timeout_s": 60
    }
    ```

This module pulls that block out of the reply text and converts it into
the ``verifier_type`` / ``verifier_inputs`` shape that
``_ensure_deliverable`` stamps on the Deliverable.

The parser is intentionally fail-soft: if the block is missing, the
JSON is malformed, fields are wrong types, or ``verifier_type`` is not
a known :class:`VerifierType`, the helper returns ``None`` and the
Deliverable's ``proof_state`` stays at ``verification_missing`` (the
project's degradable rule). A noisy reply that doesn't follow the
protocol must never break deliverable creation.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from backend.src.core.verifier.protocol import VerifierType

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class ParsedVerification:
    """Output of :func:`parse_verification_block` — unresolved cwd."""

    verifier_type: VerifierType
    inputs: dict[str, Any]


# ``\s*`` after the fence opener tolerates trailing whitespace in the
# LLM output. ``[\r\n]+`` keeps it robust against CRLF responses. The
# inner body uses non-greedy capture so multiple fenced blocks in the
# same reply do not bleed into each other.
_FENCE_RE = re.compile(
    r"```\s*bsnexus-verification\s*[\r\n]+(.+?)[\r\n]+```",
    re.DOTALL | re.IGNORECASE,
)


def parse_verification_block(reply_text: str) -> ParsedVerification | None:
    """Return the parsed verification or ``None`` on any failure path."""
    if not reply_text:
        return None
    match = _FENCE_RE.search(reply_text)
    if match is None:
        return None
    body = match.group(1).strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("verification_block_invalid_json", error=str(exc))
        return None
    if not isinstance(payload, dict):
        logger.warning("verification_block_not_object", payload_type=type(payload).__name__)
        return None

    raw_type = payload.get("verifier_type")
    if not isinstance(raw_type, str):
        logger.warning("verification_block_missing_verifier_type")
        return None
    try:
        verifier_type = VerifierType(raw_type)
    except ValueError:
        logger.warning("verification_block_unknown_verifier_type", verifier_type=raw_type)
        return None

    command = _coerce_command(payload.get("command"))
    if not command:
        logger.warning("verification_block_missing_command")
        return None

    inputs: dict[str, Any] = {"command": command}
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        inputs["cwd"] = cwd.strip()
    timeout_s = payload.get("timeout_s")
    if isinstance(timeout_s, (int, float)) and timeout_s > 0:
        inputs["timeout_s"] = int(timeout_s)
    env = payload.get("env")
    if isinstance(env, dict):
        # Coerce keys/values to strings so they survive the Redis Stream
        # JSON round-trip and the subprocess env merge.
        inputs["env"] = {str(k): str(v) for k, v in env.items() if v is not None}

    return ParsedVerification(verifier_type=verifier_type, inputs=inputs)


def _coerce_command(value: Any) -> list[str] | None:
    """Accept either a list of argv tokens or a string (left as-is for
    SubprocessVerifier's shlex.split fallback). Reject anything else."""
    if isinstance(value, list):
        if not value or any(not isinstance(part, str) for part in value):
            return None
        return [str(part) for part in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return None


def resolve_workspace_cwd(
    inputs: dict[str, Any],
    *,
    project_id: uuid.UUID,
    workspace_root: Path,
) -> dict[str, Any]:
    """Resolve a relative ``cwd`` in ``inputs`` against the project
    workspace, dropping it when the path escapes the workspace root.

    Returns a new dict with the (possibly absolute) cwd applied. The
    project workspace is the LLM's own working directory (``shell_exec``
    cwds there), so a relative path in the fenced block is interpreted
    relative to that root — same convention the LLM had when running
    ``shell_exec`` itself.
    """
    out = dict(inputs)
    raw = out.get("cwd")
    if not isinstance(raw, str) or not raw.strip():
        # Default: run the verifier from the project workspace root.
        out["cwd"] = str(workspace_root.resolve())
        return out

    cwd = raw.strip()
    if os.path.isabs(cwd):
        # Absolute paths are rejected: the LLM has no business pointing
        # the verifier outside its own sandbox.
        logger.warning(
            "verification_block_absolute_cwd_rejected",
            project_id=str(project_id),
            cwd=cwd,
        )
        out["cwd"] = str(workspace_root.resolve())
        return out

    candidate = (workspace_root / cwd).resolve()
    root = workspace_root.resolve()
    if root != candidate and root not in candidate.parents:
        logger.warning(
            "verification_block_escaping_cwd_rejected",
            project_id=str(project_id),
            cwd=cwd,
        )
        out["cwd"] = str(root)
        return out

    out["cwd"] = str(candidate)
    return out
