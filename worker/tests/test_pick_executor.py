"""Per-task CLI override — worker honors ``task["executor"]`` when set."""

from __future__ import annotations

from unittest.mock import patch

from worker.executors import CLIExecutor, ClaudeCodeExecutor, CodexExecutor
from worker.main import _pick_executor


class _FakeCLI(CLIExecutor):
    """CLIExecutor subclass whose ``resolve_cmd`` we can control in tests."""

    name = "fake"
    cli_command = "fake-cli"
    install_hint = "hint"

    def __init__(self, installed: bool = True) -> None:
        super().__init__()
        self._installed = installed

    def resolve_cmd(self) -> str | None:
        return "/usr/bin/fake-cli" if self._installed else None

    def build_args(self) -> list[str]:
        return []


def test_no_requested_returns_default():
    default = _FakeCLI()
    cache: dict = {"fake": default}
    assert _pick_executor(None, default, cache) is default


def test_same_as_default_returns_default():
    default = ClaudeCodeExecutor()
    cache: dict = {"claude_code": default}
    assert _pick_executor("claude_code", default, cache) is default


def test_known_override_uses_override_when_cli_installed():
    default = ClaudeCodeExecutor()
    cache: dict = {"claude_code": default}

    # Pretend codex is installed on PATH.
    with patch.object(CodexExecutor, "resolve_cmd", return_value="/usr/bin/codex"):
        picked = _pick_executor("codex", default, cache)

    assert isinstance(picked, CodexExecutor)
    # Cached for subsequent calls.
    assert cache["codex"] is picked


def test_unknown_requested_falls_back_to_default():
    default = ClaudeCodeExecutor()
    cache: dict = {"claude_code": default}
    picked = _pick_executor("does-not-exist", default, cache)
    assert picked is default


def test_requested_cli_not_installed_falls_back_to_default():
    default = ClaudeCodeExecutor()
    cache: dict = {"claude_code": default}

    # Codex is known to the registry but not installed on this host.
    with patch.object(CodexExecutor, "resolve_cmd", return_value=None):
        picked = _pick_executor("codex", default, cache)

    assert picked is default
    # Not cached — we want to retry in case the user installs it later.
    assert "codex" not in cache


def test_cached_override_is_reused_without_re_resolving():
    default = ClaudeCodeExecutor()
    cached_codex = CodexExecutor()
    cache: dict = {"claude_code": default, "codex": cached_codex}

    with patch("worker.main.get_executor") as mock_get:
        picked = _pick_executor("codex", default, cache)

    mock_get.assert_not_called()
    assert picked is cached_codex
