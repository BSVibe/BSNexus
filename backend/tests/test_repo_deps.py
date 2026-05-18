"""G-E — cloned-repo dependency install for the verification sandbox."""

from __future__ import annotations

import pytest

from backend.src.core.repo_deps import (
    InstallResult,
    _detect_install_command,
    ensure_repo_dependencies,
)
from backend.src.core.sandbox.protocol import SandboxResult


class _FakeSandbox:
    """Records exec calls; returns a scripted SandboxResult."""

    def __init__(self, result: SandboxResult | None = None, raises: Exception | None = None) -> None:
        self._result = result or SandboxResult(exit_code=0, stdout="", stderr="", timed_out=False)
        self._raises = raises
        self.calls: list[tuple[str, bool]] = []

    @property
    def workspace_mount(self) -> str:
        return "/work"

    async def exec(self, command: str, *, timeout_s: float, shell: bool = False) -> SandboxResult:
        self.calls.append((command, shell))
        if self._raises is not None:
            raise self._raises
        return self._result

    async def read_file(self, rel_path: str, max_bytes: int) -> bytes:  # pragma: no cover
        return b""

    async def write_file(self, rel_path: str, content: bytes) -> None:  # pragma: no cover
        return None

    async def list_dir(self, rel_path: str) -> list[str]:  # pragma: no cover
        return []


# ───────────────────────────── manifest detection ─────────────────────────


def test_detect_uv_lock(tmp_path) -> None:
    (tmp_path / "uv.lock").write_text("")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert _detect_install_command(tmp_path) == "uv sync"


def test_detect_pyproject_without_lock(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert _detect_install_command(tmp_path) == "uv sync"


def test_detect_pnpm(tmp_path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "package.json").write_text("{}")
    assert _detect_install_command(tmp_path) == "pnpm install --frozen-lockfile"


def test_detect_npm_ci(tmp_path) -> None:
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "package.json").write_text("{}")
    assert _detect_install_command(tmp_path) == "npm ci"


def test_detect_yarn(tmp_path) -> None:
    (tmp_path / "yarn.lock").write_text("")
    (tmp_path / "package.json").write_text("{}")
    assert _detect_install_command(tmp_path) == "yarn install --frozen-lockfile"


def test_detect_package_json_without_lock(tmp_path) -> None:
    (tmp_path / "package.json").write_text("{}")
    assert _detect_install_command(tmp_path) == "npm install"


def test_detect_none(tmp_path) -> None:
    (tmp_path / "README.md").write_text("hi")
    assert _detect_install_command(tmp_path) is None


# ───────────────────────────── ensure_repo_dependencies ───────────────────


@pytest.mark.asyncio
async def test_skips_when_no_manifest(tmp_path) -> None:
    sandbox = _FakeSandbox()
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "skipped"
    assert sandbox.calls == []


@pytest.mark.asyncio
async def test_installs_python_repo(tmp_path) -> None:
    (tmp_path / "uv.lock").write_text("")
    sandbox = _FakeSandbox()
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "installed"
    assert sandbox.calls == [("uv sync", True)]


@pytest.mark.asyncio
async def test_install_nonzero_exit_is_soft_failed(tmp_path) -> None:
    (tmp_path / "package.json").write_text("{}")
    sandbox = _FakeSandbox(SandboxResult(exit_code=1, stdout="", stderr="registry down", timed_out=False))
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "failed"
    assert "registry down" in (result.detail or "")


@pytest.mark.asyncio
async def test_install_timeout_is_soft_failed(tmp_path) -> None:
    (tmp_path / "uv.lock").write_text("")
    sandbox = _FakeSandbox(SandboxResult(exit_code=None, stdout="", stderr="", timed_out=True))
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "failed"
    assert "timed out" in (result.detail or "")


@pytest.mark.asyncio
async def test_install_exec_error_is_soft_failed_not_raised(tmp_path) -> None:
    (tmp_path / "uv.lock").write_text("")
    sandbox = _FakeSandbox(raises=RuntimeError("sandbox gone"))
    result = await ensure_repo_dependencies(root=tmp_path, sandbox_session=sandbox)
    assert result.status == "failed"
    assert "sandbox gone" in (result.detail or "")


def test_install_result_frozen() -> None:
    r = InstallResult(status="skipped", detail=None)
    with pytest.raises(Exception):
        r.status = "installed"  # type: ignore[misc]
