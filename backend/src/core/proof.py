from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import shlex
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAttemptStatus,
    ProofState,
)
from backend.src.models import Deliverable, ProofAttempt, ProofPolicy


class ProofPolicyError(ValueError):
    """Raised when a server-selected proof policy is invalid."""


@dataclass(frozen=True)
class SelectedProofPolicy:
    verifier_type: str
    command: tuple[str, ...]
    required_refs: tuple[str, ...]
    timeout_s: int = 120
    pass_condition: str = "exit_code_zero"


SETUP_ONLY_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("pip", "install"),
    ("python", "-m", "pip", "install"),
    ("python3", "-m", "pip", "install"),
    ("uv", "sync"),
    ("uv", "pip", "install"),
    ("npm", "install"),
    ("npm", "ci"),
    ("pnpm", "install"),
    ("yarn", "install"),
    ("corepack", "enable"),
)


async def run_proof_attempt(
    *,
    deliverable: Deliverable,
    workspace_root: Path | str,
    session: AsyncSession,
    changed_files: Sequence[str] = (),
    explicit_policy: Mapping[str, Any] | SelectedProofPolicy | None = None,
) -> ProofAttempt:
    root = Path(workspace_root)
    selection = select_proof_policy(
        workspace_root=root,
        deliverable_type=deliverable.type,
        changed_files=changed_files,
        explicit_policy=explicit_policy,
    )

    if selection is None:
        deliverable.proof_state = ProofState.human_review_required
        attempt = ProofAttempt(
            deliverable_id=deliverable.id,
            verifier_type="no_policy",
            inputs=_attempt_inputs(root=root, changed_files=changed_files, command=()),
            status=ProofAttemptStatus.human_review_required,
            proof_summary="No deterministic proof policy matched this deliverable",
            proof_refs=[],
            completed_at=datetime.now(UTC),
        )
        session.add(attempt)
        await session.commit()
        await session.refresh(attempt)
        return attempt

    policy = ProofPolicy(
        deliverable_type=deliverable.type,
        verifier_type=selection.verifier_type,
        command_template=list(selection.command),
        required_refs=list(selection.required_refs),
        timeout_s=selection.timeout_s,
        pass_condition=selection.pass_condition,
    )
    session.add(policy)
    await session.flush()

    deliverable.proof_policy_id = policy.id
    deliverable.proof_state = ProofState.verifying
    deliverable.status = DeliverableStatus.verifying
    attempt = ProofAttempt(
        deliverable_id=deliverable.id,
        verifier_type=selection.verifier_type,
        inputs=_attempt_inputs(root=root, changed_files=changed_files, command=selection.command),
        status=ProofAttemptStatus.running,
    )
    session.add(attempt)
    await session.flush()

    exit_code, output = await _run_command(selection.command, cwd=root, timeout_s=selection.timeout_s)
    attempt.exit_code = exit_code
    attempt.completed_at = datetime.now(UTC)
    attempt.proof_refs = [
        {
            "kind": "verifier_command",
            "command": list(selection.command),
            "required_refs": list(selection.required_refs),
        }
    ]

    if exit_code == 0:
        attempt.status = ProofAttemptStatus.verified
        attempt.proof_summary = f"Verifier command passed: {shlex.join(selection.command)}"
        deliverable.proof_state = ProofState.verified
        deliverable.status = DeliverableStatus.review_ready
    else:
        attempt.status = ProofAttemptStatus.failed
        attempt.proof_summary = f"Verifier command failed: {shlex.join(selection.command)}\n{output}"
        deliverable.proof_state = ProofState.verification_failed

    await session.commit()
    await session.refresh(attempt)
    await session.refresh(deliverable)
    return attempt


def select_proof_policy(
    *,
    workspace_root: Path | str,
    deliverable_type: DeliverableType,
    changed_files: Sequence[str] = (),
    explicit_policy: Mapping[str, Any] | SelectedProofPolicy | None = None,
) -> SelectedProofPolicy | None:
    root = Path(workspace_root)
    if explicit_policy is not None:
        return _coerce_explicit_policy(explicit_policy)

    if deliverable_type not in {DeliverableType.code, DeliverableType.pr, DeliverableType.preview}:
        return None

    python_policy = _python_policy(root)
    node_policy = _node_policy(root)
    changed = tuple(changed_files)

    if changed:
        if python_policy is not None and any(_is_python_change(path) for path in changed):
            return python_policy
        if node_policy is not None and any(_is_node_change(path) for path in changed):
            return node_policy

    return python_policy or node_policy


def is_setup_only_command(command: str | Sequence[str]) -> bool:
    parts = tuple(shlex.split(command) if isinstance(command, str) else command)
    normalized = tuple(part.strip() for part in parts if part.strip())
    return any(normalized[: len(prefix)] == prefix for prefix in SETUP_ONLY_COMMANDS)


def _coerce_explicit_policy(policy: Mapping[str, Any] | SelectedProofPolicy) -> SelectedProofPolicy:
    if isinstance(policy, SelectedProofPolicy):
        selection = policy
    else:
        raw_command = policy.get("command") or policy.get("command_template")
        if raw_command is None:
            raise ProofPolicyError("Explicit proof policy requires a command")
        command = _normalize_command(raw_command)
        selection = SelectedProofPolicy(
            verifier_type=str(policy.get("verifier_type") or "explicit_command"),
            command=command,
            required_refs=tuple(str(ref) for ref in policy.get("required_refs", ())),
            timeout_s=int(policy.get("timeout_s", 120)),
            pass_condition=str(policy.get("pass_condition") or "exit_code_zero"),
        )

    if not selection.command:
        raise ProofPolicyError("Proof policy command cannot be empty")
    if is_setup_only_command(selection.command):
        raise ProofPolicyError("Setup-only commands cannot be used as verifier proof")
    if selection.pass_condition != "exit_code_zero":
        raise ProofPolicyError("G0 verifier proof only supports exit_code_zero")
    return selection


def _python_policy(root: Path) -> SelectedProofPolicy | None:
    refs = [name for name in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg") if (root / name).exists()]
    if not refs and not (root / "tests").is_dir():
        return None
    if (root / "tests").is_dir():
        refs.append("tests/")
    return SelectedProofPolicy(
        verifier_type="python_test",
        command=("python", "-m", "pytest"),
        required_refs=tuple(refs),
        timeout_s=300,
    )


def _node_policy(root: Path) -> SelectedProofPolicy | None:
    package_json = root / "package.json"
    if not package_json.exists():
        return None

    scripts = _package_scripts(package_json)
    manager = "pnpm" if (root / "pnpm-lock.yaml").exists() else "npm"
    refs = ["package.json"]
    if manager == "pnpm":
        refs.append("pnpm-lock.yaml")

    test_script = scripts.get("test")
    if test_script and not _is_default_npm_test_script(test_script):
        return SelectedProofPolicy(
            verifier_type="node_test",
            command=(manager, "test"),
            required_refs=tuple(refs),
            timeout_s=300,
        )

    if scripts.get("build"):
        command = (manager, "build") if manager == "pnpm" else ("npm", "run", "build")
        return SelectedProofPolicy(
            verifier_type="node_build",
            command=command,
            required_refs=tuple(refs),
            timeout_s=300,
        )

    return None


def _package_scripts(package_json: Path) -> dict[str, str]:
    try:
        payload = json.loads(package_json.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(key): str(value) for key, value in scripts.items()}


def _normalize_command(command: Any) -> tuple[str, ...]:
    if isinstance(command, str):
        return tuple(shlex.split(command))
    if isinstance(command, Sequence):
        return tuple(str(part) for part in command)
    raise ProofPolicyError("Proof policy command must be a string or sequence")


def _is_python_change(path: str) -> bool:
    return (
        path.endswith(".py")
        or path in {"pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"}
        or path.startswith("tests/")
    )


def _is_node_change(path: str) -> bool:
    return path.endswith((".js", ".jsx", ".ts", ".tsx")) or path in {
        "package.json",
        "pnpm-lock.yaml",
        "package-lock.json",
    }


def _is_default_npm_test_script(script: str) -> bool:
    normalized = script.strip().lower()
    return "no test specified" in normalized and normalized.startswith("echo")


def _attempt_inputs(*, root: Path, changed_files: Sequence[str], command: Sequence[str]) -> dict[str, Any]:
    return {
        "workspace_root": str(root),
        "changed_files": list(changed_files),
        "command": list(command),
    }


async def _run_command(command: Sequence[str], *, cwd: Path, timeout_s: int) -> tuple[int | None, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return 127, str(exc)

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        return None, _tail_output(stdout, stderr, suffix=f"Timed out after {timeout_s}s")

    return process.returncode, _tail_output(stdout, stderr)


def _tail_output(stdout: bytes, stderr: bytes, *, suffix: str | None = None) -> str:
    output = "\n".join(chunk.decode("utf-8", errors="replace") for chunk in (stdout, stderr) if chunk)
    if suffix:
        output = f"{output}\n{suffix}" if output else suffix
    return output[-4000:]
