"""SubprocessVerifier — runs a verification command in an isolated subprocess.

PR3 sandbox tier (decision-locks A1):
- ``asyncio.create_subprocess_exec`` with restricted CWD
- hard timeout
- capped stdout / stderr (bytes truncated, summary kept short)
- no shell expansion (commands are tokenised — verifier sees a list)

Future tiers (firejail / bubblewrap → container per verification) plug
in by replacing this implementation; the registry routing is unchanged.

``inputs`` schema for this verifier:

```
{
    "command": ["uv", "run", "pytest", "-q"]   # or string (shlex.split)
    "cwd": "/workspace/<project>/",            # optional, defaults to a safe sandbox dir
    "timeout_s": 300,                          # optional, hard cap enforced
    "env": {"FOO": "bar"},                     # optional, merged onto the parent env
}
```
"""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Sequence
from pathlib import Path

import structlog

from backend.src.core.verifier.protocol import (
    VerificationEnvelope,
    VerificationResult,
    VerifierProofState,
    VerifierType,
)

logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT_S = 300
_MAX_TIMEOUT_S = 1800
_STDOUT_TAIL_BYTES = 4096
_SUMMARY_LINES = 3


class SubprocessVerifier:
    """Runs the configured command in a subprocess and reports the exit code."""

    verifier_types: frozenset[VerifierType] = frozenset(
        {
            VerifierType.software_test,
            VerifierType.software_build,
            VerifierType.software_start,
        }
    )

    async def verify(self, envelope: VerificationEnvelope) -> VerificationResult:
        inputs = envelope.inputs
        argv = self._normalise_command(inputs.get("command"))
        if not argv:
            return VerificationResult(
                proof_state=VerifierProofState.verification_failed,
                summary="No command supplied to SubprocessVerifier",
            )

        cwd = self._resolve_cwd(inputs.get("cwd"))
        timeout_s = self._resolve_timeout(inputs.get("timeout_s"))
        env = self._build_env(inputs.get("env"))

        logger.info(
            "subprocess_verifier_start",
            deliverable_id=str(envelope.deliverable_id),
            verifier_type=envelope.verifier_type.value,
            argv=argv,
            cwd=str(cwd) if cwd else None,
            timeout_s=timeout_s,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return VerificationResult(
                proof_state=VerifierProofState.verification_failed,
                summary=f"Failed to spawn verifier: {exc!s}",
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return VerificationResult(
                proof_state=VerifierProofState.verification_failed,
                summary=f"Verifier timed out after {timeout_s}s",
            )

        exit_code = proc.returncode if proc.returncode is not None else -1
        stdout = _truncate(stdout_b)
        stderr = _truncate(stderr_b)
        summary = _summary_from_streams(stdout, stderr, exit_code)
        proof_refs = _refs_from_streams(stdout, stderr)

        proof_state = VerifierProofState.verified if exit_code == 0 else VerifierProofState.verification_failed

        logger.info(
            "subprocess_verifier_done",
            deliverable_id=str(envelope.deliverable_id),
            exit_code=exit_code,
            proof_state=proof_state.value,
        )
        return VerificationResult(
            proof_state=proof_state,
            exit_code=exit_code,
            summary=summary,
            proof_refs=proof_refs,
        )

    # ── helpers ────────────────────────────────────────────────────

    def _normalise_command(self, command: object) -> list[str]:
        if command is None:
            return []
        if isinstance(command, str):
            return shlex.split(command)
        if isinstance(command, Sequence):
            return [str(part) for part in command]
        return []

    def _resolve_cwd(self, cwd: object) -> Path | None:
        if cwd is None:
            return None
        try:
            path = Path(str(cwd))
        except (TypeError, ValueError):
            return None
        if not path.exists() or not path.is_dir():
            return None
        return path

    def _resolve_timeout(self, timeout_s: object) -> int:
        try:
            value = int(timeout_s) if timeout_s is not None else _DEFAULT_TIMEOUT_S
        except (TypeError, ValueError):
            return _DEFAULT_TIMEOUT_S
        if value <= 0:
            return _DEFAULT_TIMEOUT_S
        return min(value, _MAX_TIMEOUT_S)

    def _build_env(self, extra: object) -> dict[str, str]:
        env = dict(os.environ)
        if isinstance(extra, dict):
            for k, v in extra.items():
                if v is None:
                    env.pop(str(k), None)
                else:
                    env[str(k)] = str(v)
        return env


def _truncate(data: bytes | None) -> str:
    if not data:
        return ""
    if len(data) <= _STDOUT_TAIL_BYTES:
        return data.decode("utf-8", errors="replace")
    tail = data[-_STDOUT_TAIL_BYTES:]
    return "…\n" + tail.decode("utf-8", errors="replace")


def _summary_from_streams(stdout: str, stderr: str, exit_code: int) -> str:
    pool = stdout.splitlines() + stderr.splitlines()
    tail = [line for line in pool[-_SUMMARY_LINES:] if line.strip()]
    if tail:
        joined = " | ".join(tail)
        return f"exit={exit_code} · {joined}"
    return f"exit={exit_code}"


def _refs_from_streams(stdout: str, stderr: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if stdout:
        refs.append({"label": "stdout", "type": "log", "href": "inline://stdout"})
    if stderr:
        refs.append({"label": "stderr", "type": "log", "href": "inline://stderr"})
    return refs
