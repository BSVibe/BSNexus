import asyncio
import re
import shutil
import sys
from typing import Any

import structlog

from backend.src.config import settings
from backend.src.prompts.loader import get_prompt

from .base import ExecutionResult, ReviewResult

logger = structlog.get_logger(__name__)


class ClaudeCodeExecutor:
    """Claude Code CLI executor."""

    def __init__(self, workspace_dir: str | None = None) -> None:
        self.workspace_dir = workspace_dir or settings.workspace_dir
        self._claude_cmd = self._resolve_claude_cmd()
        self._rate_limit_max_retries = settings.rate_limit_retry_count
        self._rate_limit_wait_seconds = settings.rate_limit_wait_seconds
        self._execution_timeout_seconds = settings.execution_timeout_seconds
        self._total_execution_timeout_seconds = settings.total_execution_timeout_seconds
        self._skip_permissions = settings.executor_skip_permissions

    def supported_task_types(self) -> list[str]:
        """Return task types this executor can handle."""
        return ["coding", "refactor", "bugfix", "test"]

    @staticmethod
    def _resolve_claude_cmd() -> str:
        """Resolve the claude CLI command path."""
        resolved = shutil.which("claude")
        if resolved:
            return resolved
        if sys.platform == "win32":
            resolved = shutil.which("claude.cmd")
            if resolved:
                return resolved
        return "claude"

    async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
        """Execute coding task via Claude Code CLI, with rate limit retry."""
        task_id = context.get("task_id", "unknown")
        workspace = context.get("workspace_dir", self.workspace_dir)
        return await self._execute_with_rate_limit_retry(prompt, task_id, workspace)

    async def _execute_with_rate_limit_retry(
        self,
        prompt: str,
        task_id: str,
        workspace: str,
    ) -> ExecutionResult:
        """Run CLI, retrying on rate limit until reset.

        Applies a total timeout across all retries to prevent unbounded execution.
        """
        try:
            return await asyncio.wait_for(
                self._retry_loop(prompt, task_id, workspace),
                timeout=self._total_execution_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error(
                "claude-cli: total timeout after %ds task_id=%s",
                self._total_execution_timeout_seconds,
                task_id,
            )
            return ExecutionResult(
                success=False,
                error_message=f"Total execution timed out after {self._total_execution_timeout_seconds}s",
                error_category="environment",
            )

    async def _retry_loop(
        self,
        prompt: str,
        task_id: str,
        workspace: str,
    ) -> ExecutionResult:
        """Inner retry loop for rate limit handling."""
        result: ExecutionResult | None = None
        for attempt in range(self._rate_limit_max_retries + 1):
            result = await self._run_cli(prompt, task_id, workspace)
            if result.success:
                return result
            output = (result.stdout or "") + (result.stderr or "")
            wait_seconds = self._parse_rate_limit_wait_instance(output)
            if wait_seconds is None:
                return result
            if attempt >= self._rate_limit_max_retries:
                logger.error(
                    "claude-cli: rate limit retry exhausted after %d attempts task_id=%s",
                    self._rate_limit_max_retries,
                    task_id,
                )
                return result
            logger.warning(
                "claude-cli: rate limited, waiting %ds (attempt %d/%d) task_id=%s",
                wait_seconds,
                attempt + 1,
                self._rate_limit_max_retries,
                task_id,
            )
            await asyncio.sleep(wait_seconds)
        assert result is not None
        return result

    def _parse_rate_limit_wait_instance(self, output: str) -> int | None:
        """Detect rate limit from CLI output."""
        lower = output.lower()
        if "hit your limit" in lower or "rate limit" in lower:
            return self._rate_limit_wait_seconds
        return None

    async def _run_cli(self, prompt: str, task_id: str, workspace: str) -> ExecutionResult:
        """Single CLI invocation."""
        process: asyncio.subprocess.Process | None = None
        try:
            logger.info("claude-cli: starting task_id=%s cwd=%s", task_id, workspace)
            prompt_bytes = prompt.encode("utf-8")
            cmd_args = [self._claude_cmd, "--print"]
            if self._skip_permissions:
                cmd_args.append("--dangerously-skip-permissions")
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                cwd=workspace,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=prompt_bytes),
                timeout=self._execution_timeout_seconds,
            )

            rc = process.returncode
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            logger.info("claude-cli: finished rc=%d stdout=%d bytes stderr=%d bytes", rc, len(stdout), len(stderr))

            return ExecutionResult(
                success=rc == 0,
                stdout=out,
                stderr=err,
                error_message=err if rc != 0 else None,
                error_category="" if rc == 0 else "tool",
            )

        except asyncio.TimeoutError:
            logger.error("claude-cli: TIMEOUT after %ds task_id=%s", self._execution_timeout_seconds, task_id)
            return ExecutionResult(
                success=False,
                error_message=f"Execution timed out after {self._execution_timeout_seconds}s",
                error_category="environment",
            )
        except (FileNotFoundError, PermissionError, OSError, UnicodeEncodeError) as e:
            logger.error("claude-cli: environment error task_id=%s error=%s", task_id, e)
            return ExecutionResult(
                success=False,
                error_message=str(e),
                error_category="environment",
            )
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:
                    pass

    async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult:
        """Execute code review via Claude Code CLI."""
        task_id = context.get("task_id", "unknown")
        logger.info("claude-cli: review starting task_id=%s", task_id)

        review_prompt = get_prompt("review", "code_review").format(task_prompt=prompt)
        result = await self.execute(review_prompt, context)

        if not result.success:
            logger.warning("claude-cli: review execution failed task_id=%s", task_id)
            return ReviewResult(passed=False, error_message=result.error_message, error_category=result.error_category)

        output = result.stdout.strip()
        passed = self._parse_review_verdict(output)
        logger.info("claude-cli: review result=%s task_id=%s", "PASS" if passed else "FAIL", task_id)

        return ReviewResult(passed=passed, feedback=output)

    @staticmethod
    def _parse_review_verdict(output: str) -> bool:
        """Parse PASS/FAIL verdict from review output.

        Searches from the end of the output for a verdict line.
        Strips common markdown formatting (bold, code, headers, quotes)
        before matching.
        """
        lines = output.strip().splitlines()

        for line in reversed(lines):
            # Strip markdown formatting: *, `, #, >, -, ~, =, |, whitespace
            cleaned = re.sub(r"[*`#>~=|_\-]", "", line).strip().upper()
            if not cleaned:
                continue
            if re.match(r"^(VERDICT|RESULT)\s*:?\s*PASS", cleaned):
                return True
            if re.match(r"^(VERDICT|RESULT)\s*:?\s*FAIL", cleaned):
                return False

        for line in reversed(lines):
            cleaned = re.sub(r"[*`#>~=|_\-]", "", line).strip().upper()
            if not cleaned:
                continue
            if re.match(r"^PASS\b", cleaned):
                return True
            if re.match(r"^FAIL\b", cleaned):
                return False

        # Also search for verdict anywhere in the last 20 lines as a fallback
        for line in reversed(lines[-20:]):
            upper = line.upper()
            if re.search(r"\bVERDICT\s*:?\s*PASS\b", upper):
                return True
            if re.search(r"\bVERDICT\s*:?\s*FAIL\b", upper):
                return False

        return False
