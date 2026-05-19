"""Cloned-repo environment setup — devcontainer-honoring + heuristic fallback.

Environment setup is repo-defined: a `.devcontainer/devcontainer.json`
declares the lifecycle commands. The manifest heuristic is only the
fallback for a repo that has not had a devcontainer authored yet.
"""

from __future__ import annotations

import json

import pytest

from backend.src.core.repo_deps import (
    InstallResult,
    _devcontainer_setup_commands,
    _heuristic_install_command,
    ensure_repo_dependencies,
)
from backend.src.core.sandbox.protocol import SandboxResult


class _FakeSandbox:
    """Records exec calls; returns a scripted SandboxResult per call."""

    def __init__(self, result: SandboxResult | None = None, raises: Exception | None = None) -> None:
        self._result = result or SandboxResult(exit_code=0, stdout="", stderr="", timed_out=False)
        self._raises = raises
        self.calls: list[str] = []

    @property
    def workspace_mount(self) -> str:
        return "/work"

    async def exec(self, command: str, *, timeout_s: float, shell: bool = False) -> SandboxResult:
        self.calls.append(command)
        if self._raises is not None:
            raise self._raises
        return self._result

    async def read_file(self, rel_path: str, max_bytes: int) -> bytes:  # pragma: no cover
        return b""

    async def write_file(self, rel_path: str, content: bytes) -> None:  # pragma: no cover
        return None

    async def list_dir(self, rel_path: str) -> list[str]:  # pragma: no cover
        return []


def _write_devcontainer(root, config: dict | str) -> None:
    dc = root / ".devcontainer"
    dc.mkdir(parents=True, exist_ok=True)
    text = config if isinstance(config, str) else json.dumps(config)
    (dc / "devcontainer.json").write_text(text)


# ───────────────────────── devcontainer parsing ───────────────────────────


def test_devcontainer_postcreatecommand_string(tmp_path):
    _write_devcontainer(tmp_path, {"postCreateCommand": "pnpm install"})
    assert _devcontainer_setup_commands(tmp_path) == ["pnpm install"]


def test_devcontainer_lifecycle_order(tmp_path):
    _write_devcontainer(
        tmp_path,
        {"postCreateCommand": "pnpm install", "onCreateCommand": "echo hi"},
    )
    # onCreateCommand runs before postCreateCommand regardless of file order.
    assert _devcontainer_setup_commands(tmp_path) == ["echo hi", "pnpm install"]


def test_devcontainer_argv_array(tmp_path):
    _write_devcontainer(tmp_path, {"postCreateCommand": ["uv", "sync"]})
    assert _devcontainer_setup_commands(tmp_path) == ["uv sync"]


def test_devcontainer_object_commands(tmp_path):
    _write_devcontainer(tmp_path, {"postCreateCommand": {"deps": "uv sync", "build": "make"}})
    assert sorted(_devcontainer_setup_commands(tmp_path)) == ["make", "uv sync"]


def test_devcontainer_jsonc_with_comments_and_trailing_comma(tmp_path):
    _write_devcontainer(
        tmp_path,
        '{\n  // dev container\n  "image": "x", /* base */\n  "postCreateCommand": "uv sync",\n}',
    )
    assert _devcontainer_setup_commands(tmp_path) == ["uv sync"]


def test_devcontainer_absent_returns_empty(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert _devcontainer_setup_commands(tmp_path) == []


# ───────────────────────── heuristic fallback ─────────────────────────────


def test_heuristic_uv(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    assert _heuristic_install_command(tmp_path) == "uv sync"


def test_heuristic_pnpm(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert _heuristic_install_command(tmp_path) == "pnpm install --frozen-lockfile"


def test_heuristic_none(tmp_path):
    (tmp_path / "README.md").write_text("hi")
    assert _heuristic_install_command(tmp_path) is None


# ───────────────────────── ensure_repo_dependencies ───────────────────────


@pytest.mark.asyncio
async def test_devcontainer_wins_over_heuristic(tmp_path):
    """A repo with both a devcontainer and a manifest uses the
    devcontainer — repo-defined beats infra-guessed."""
    _write_devcontainer(tmp_path, {"postCreateCommand": "pnpm install --frozen-lockfile"})
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")  # would heuristic to `uv sync`
    sandbox = _FakeSandbox()
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "installed"
    assert result.source == "devcontainer"
    assert sandbox.calls == ["pnpm install --frozen-lockfile"]


@pytest.mark.asyncio
async def test_falls_back_to_heuristic_without_devcontainer(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    sandbox = _FakeSandbox()
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "installed"
    assert result.source == "heuristic"
    assert sandbox.calls == ["uv sync"]


@pytest.mark.asyncio
async def test_skips_when_no_devcontainer_and_no_manifest(tmp_path):
    sandbox = _FakeSandbox()
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "skipped"
    assert sandbox.calls == []


@pytest.mark.asyncio
async def test_devcontainer_command_failure_is_soft_failed(tmp_path):
    _write_devcontainer(tmp_path, {"postCreateCommand": "pnpm install"})
    sandbox = _FakeSandbox(SandboxResult(exit_code=1, stdout="", stderr="registry down", timed_out=False))
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "failed"
    assert result.source == "devcontainer"
    assert "registry down" in (result.detail or "")


@pytest.mark.asyncio
async def test_setup_error_is_soft_failed_not_raised(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    sandbox = _FakeSandbox(raises=RuntimeError("sandbox gone"))
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "failed"
    assert "sandbox gone" in (result.detail or "")


def test_install_result_frozen():
    r = InstallResult(status="skipped", detail=None)
    with pytest.raises(Exception):
        r.status = "installed"  # type: ignore[misc]
