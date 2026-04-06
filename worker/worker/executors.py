"""CLI executor adapters — pluggable backends for the worker agent.

Each executor wraps a coding CLI tool (Claude Code, Codex, OpenCode, etc.)
with a unified interface: take a prompt string, return a result dict.

Adding a new executor:
1. Create a subclass of CLIExecutor
2. Register it in EXECUTOR_REGISTRY
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ExecResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


class CLIExecutor:
    """Base class for CLI-based coding executors."""

    name: str = "base"
    cli_command: str = ""
    install_hint: str = ""

    def __init__(self, timeout: int = 3600, skip_permissions: bool = True) -> None:
        self.timeout = timeout
        self.skip_permissions = skip_permissions

    def resolve_cmd(self) -> str | None:
        """Find the CLI binary in PATH."""
        return shutil.which(self.cli_command)

    def build_args(self) -> list[str]:
        """Build command-line arguments. Override in subclasses."""
        raise NotImplementedError

    async def execute(self, prompt: str, cwd: str) -> ExecResult:
        """Execute a task via the CLI tool."""
        cmd_path = self.resolve_cmd()
        if not cmd_path:
            return ExecResult(
                success=False,
                error=f"'{self.cli_command}' not found. Install: {self.install_hint}",
            )

        args = [cmd_path] + self.build_args()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=self.timeout,
            )
            return ExecResult(
                success=proc.returncode == 0,
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace"),
                error=stderr.decode(errors="replace") if proc.returncode != 0 else None,
            )
        except asyncio.TimeoutError:
            return ExecResult(success=False, error=f"Timeout after {self.timeout}s")
        except FileNotFoundError:
            return ExecResult(success=False, error=f"'{self.cli_command}' not found in PATH")


class ClaudeCodeExecutor(CLIExecutor):
    """Claude Code CLI — claude --print"""

    name = "claude_code"
    cli_command = "claude"
    install_hint = "npm install -g @anthropic-ai/claude-code"

    def build_args(self) -> list[str]:
        args = ["--print"]
        if self.skip_permissions:
            args.append("--dangerously-skip-permissions")
        return args


class CodexExecutor(CLIExecutor):
    """OpenAI Codex CLI — codex --quiet"""

    name = "codex"
    cli_command = "codex"
    install_hint = "npm install -g @openai/codex"

    def build_args(self) -> list[str]:
        args = ["--quiet"]
        if self.skip_permissions:
            args.append("--full-auto")
        return args


class OpenCodeExecutor(CLIExecutor):
    """OpenCode CLI — opencode run"""

    name = "opencode"
    cli_command = "opencode"
    install_hint = "go install github.com/opencode-ai/opencode@latest"

    def build_args(self) -> list[str]:
        return ["run"]


# ─── Registry ─────────────────────────────────────────────────────

EXECUTOR_REGISTRY: dict[str, type[CLIExecutor]] = {
    "claude_code": ClaudeCodeExecutor,
    "codex": CodexExecutor,
    "opencode": OpenCodeExecutor,
}

# Preference order when multiple CLIs are installed
EXECUTOR_PRIORITY = ["claude_code", "codex", "opencode"]


def get_executor(name: str, **kwargs) -> CLIExecutor:
    """Get an executor by name. Raises KeyError if not found."""
    cls = EXECUTOR_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(EXECUTOR_REGISTRY))
        raise KeyError(f"Unknown executor '{name}'. Available: {available}")
    return cls(**kwargs)


def detect_available() -> list[str]:
    """Return installed executors, sorted by preference (claude_code > codex > opencode)."""
    available = []
    for name in EXECUTOR_PRIORITY:
        cls = EXECUTOR_REGISTRY.get(name)
        if cls and cls().resolve_cmd():
            available.append(name)
    return available
