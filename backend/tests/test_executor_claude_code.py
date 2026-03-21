"""Comprehensive tests for ClaudeCodeExecutor."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.executor.base import ExecutionResult, ReviewResult
from backend.src.core.executor.claude_code import ClaudeCodeExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor(
    workspace_dir: str = "/workspace",
    rate_limit_retry_count: int = 3,
    rate_limit_wait_seconds: int = 1,
    execution_timeout_seconds: int = 60,
) -> ClaudeCodeExecutor:
    """Create an executor with controlled settings, bypassing real binary lookup."""
    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch("backend.src.core.executor.claude_code.settings") as mock_settings:
            mock_settings.rate_limit_retry_count = rate_limit_retry_count
            mock_settings.rate_limit_wait_seconds = rate_limit_wait_seconds
            mock_settings.execution_timeout_seconds = execution_timeout_seconds
            mock_settings.total_execution_timeout_seconds = 300
            mock_settings.executor_skip_permissions = True
            executor = ClaudeCodeExecutor(workspace_dir=workspace_dir)
    return executor


def _mock_process(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> AsyncMock:
    """Build a mock subprocess process."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


# ===========================================================================
# _resolve_claude_cmd
# ===========================================================================


class TestResolveClaueCmd:
    """Tests for _resolve_claude_cmd static method."""

    def test_found_in_path(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = ClaudeCodeExecutor._resolve_claude_cmd()
        assert result == "/usr/local/bin/claude"

    def test_not_found_falls_back_to_bare_name(self) -> None:
        with patch("shutil.which", return_value=None):
            result = ClaudeCodeExecutor._resolve_claude_cmd()
        assert result == "claude"

    def test_windows_fallback_to_claude_cmd(self) -> None:
        """On Windows, if 'claude' is not found, try 'claude.cmd'."""
        def _which(name: str) -> str | None:
            if name == "claude":
                return None
            if name == "claude.cmd":
                return r"C:\Users\dev\claude.cmd"
            return None

        with patch("shutil.which", side_effect=_which), patch.object(sys, "platform", "win32"):
            result = ClaudeCodeExecutor._resolve_claude_cmd()
        assert result == r"C:\Users\dev\claude.cmd"

    def test_windows_no_claude_cmd_either(self) -> None:
        with patch("shutil.which", return_value=None), patch.object(sys, "platform", "win32"):
            result = ClaudeCodeExecutor._resolve_claude_cmd()
        assert result == "claude"

    def test_non_windows_skips_claude_cmd(self) -> None:
        """On non-Windows, never try 'claude.cmd'."""
        calls: list[str] = []

        def _which(name: str) -> str | None:
            calls.append(name)
            return None

        with patch("shutil.which", side_effect=_which), patch.object(sys, "platform", "linux"):
            ClaudeCodeExecutor._resolve_claude_cmd()

        assert "claude.cmd" not in calls


# ===========================================================================
# _run_cli
# ===========================================================================


class TestRunCli:
    """Tests for the _run_cli method."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"task done", stderr=b"", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor._run_cli("do stuff", "task-1", "/workspace")

        assert result.success is True
        assert result.stdout == "task done"
        assert result.stderr == ""
        assert result.error_message is None
        assert result.error_category == ""

    @pytest.mark.asyncio
    async def test_failure_nonzero_returncode(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"", stderr=b"error occurred", returncode=1)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor._run_cli("do stuff", "task-1", "/workspace")

        assert result.success is False
        assert result.error_message == "error occurred"
        assert result.error_category == "tool"

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        executor = _make_executor(execution_timeout_seconds=1)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.returncode = None

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await executor._run_cli("slow prompt", "task-2", "/workspace")

        assert result.success is False
        assert "timed out" in result.error_message
        assert result.error_category == "environment"

    @pytest.mark.asyncio
    async def test_file_not_found_error(self) -> None:
        executor = _make_executor()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("claude not found")):
            result = await executor._run_cli("prompt", "task-3", "/workspace")

        assert result.success is False
        assert "claude not found" in result.error_message
        assert result.error_category == "environment"

    @pytest.mark.asyncio
    async def test_permission_error(self) -> None:
        executor = _make_executor()

        with patch("asyncio.create_subprocess_exec", side_effect=PermissionError("not executable")):
            result = await executor._run_cli("prompt", "task-4", "/workspace")

        assert result.success is False
        assert "not executable" in result.error_message
        assert result.error_category == "environment"

    @pytest.mark.asyncio
    async def test_os_error(self) -> None:
        executor = _make_executor()

        with patch("asyncio.create_subprocess_exec", side_effect=OSError("bad fd")):
            result = await executor._run_cli("prompt", "task-5", "/workspace")

        assert result.success is False
        assert "bad fd" in result.error_message

    @pytest.mark.asyncio
    async def test_stderr_captured_on_success(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"ok", stderr=b"warning: something", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor._run_cli("prompt", "task-6", "/workspace")

        assert result.success is True
        assert result.stderr == "warning: something"
        assert result.error_message is None

    @pytest.mark.asyncio
    async def test_unicode_decode_errors_replaced(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"ok\xff\xfe", stderr=b"\x80\x81", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor._run_cli("prompt", "task-7", "/workspace")

        assert result.success is True
        # Should not raise; invalid bytes replaced
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)


# ===========================================================================
# _parse_rate_limit_wait_instance
# ===========================================================================


class TestParseRateLimitWait:
    """Tests for _parse_rate_limit_wait_instance."""

    def test_hit_your_limit(self) -> None:
        executor = _make_executor(rate_limit_wait_seconds=42)
        result = executor._parse_rate_limit_wait_instance("You have hit your limit. Please wait.")
        assert result == 42

    def test_rate_limit_lowercase(self) -> None:
        executor = _make_executor(rate_limit_wait_seconds=10)
        result = executor._parse_rate_limit_wait_instance("Error: rate limit exceeded")
        assert result == 10

    def test_rate_limit_mixed_case(self) -> None:
        executor = _make_executor(rate_limit_wait_seconds=5)
        result = executor._parse_rate_limit_wait_instance("Rate Limit reached, please wait")
        assert result == 5

    def test_no_rate_limit_returns_none(self) -> None:
        executor = _make_executor()
        result = executor._parse_rate_limit_wait_instance("Normal error: something went wrong")
        assert result is None

    def test_empty_string(self) -> None:
        executor = _make_executor()
        result = executor._parse_rate_limit_wait_instance("")
        assert result is None

    def test_hit_your_limit_in_stderr_portion(self) -> None:
        executor = _make_executor(rate_limit_wait_seconds=15)
        combined = "stdout part\nstderr: you have hit your limit"
        result = executor._parse_rate_limit_wait_instance(combined)
        assert result == 15


# ===========================================================================
# _execute_with_rate_limit_retry
# ===========================================================================


class TestExecuteWithRateLimitRetry:
    """Tests for _execute_with_rate_limit_retry."""

    @pytest.mark.asyncio
    async def test_success_first_try(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"done", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor._execute_with_rate_limit_retry("prompt", "task-1", "/workspace")

        assert result.success is True

    @pytest.mark.asyncio
    async def test_retry_on_rate_limit_then_success(self) -> None:
        executor = _make_executor(rate_limit_retry_count=3, rate_limit_wait_seconds=0)

        rate_limited = ExecutionResult(
            success=False, stdout="hit your limit", stderr="", error_category="tool"
        )
        success = ExecutionResult(success=True, stdout="done", stderr="")

        call_count = 0

        async def fake_run_cli(prompt: str, task_id: str, workspace: str) -> ExecutionResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return rate_limited
            return success

        executor._run_cli = fake_run_cli  # type: ignore[assignment]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await executor._execute_with_rate_limit_retry("prompt", "task-1", "/workspace")

        assert result.success is True
        assert call_count == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self) -> None:
        executor = _make_executor(rate_limit_retry_count=2, rate_limit_wait_seconds=0)

        rate_limited = ExecutionResult(
            success=False, stdout="rate limit exceeded", stderr="", error_category="tool"
        )

        async def fake_run_cli(prompt: str, task_id: str, workspace: str) -> ExecutionResult:
            return rate_limited

        executor._run_cli = fake_run_cli  # type: ignore[assignment]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await executor._execute_with_rate_limit_retry("prompt", "task-1", "/workspace")

        assert result.success is False

    @pytest.mark.asyncio
    async def test_non_rate_limit_failure_no_retry(self) -> None:
        executor = _make_executor(rate_limit_retry_count=3)

        failure = ExecutionResult(
            success=False, stdout="", stderr="syntax error", error_category="tool"
        )

        call_count = 0

        async def fake_run_cli(prompt: str, task_id: str, workspace: str) -> ExecutionResult:
            nonlocal call_count
            call_count += 1
            return failure

        executor._run_cli = fake_run_cli  # type: ignore[assignment]

        result = await executor._execute_with_rate_limit_retry("prompt", "task-1", "/workspace")

        assert result.success is False
        assert call_count == 1  # No retry for non-rate-limit errors

    @pytest.mark.asyncio
    async def test_rate_limit_sleep_duration(self) -> None:
        executor = _make_executor(rate_limit_retry_count=1, rate_limit_wait_seconds=42)

        rate_limited = ExecutionResult(
            success=False, stdout="hit your limit", stderr="", error_category="tool"
        )
        success = ExecutionResult(success=True, stdout="ok", stderr="")

        calls = 0

        async def fake_run_cli(prompt: str, task_id: str, workspace: str) -> ExecutionResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                return rate_limited
            return success

        executor._run_cli = fake_run_cli  # type: ignore[assignment]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await executor._execute_with_rate_limit_retry("prompt", "task-1", "/workspace")

        mock_sleep.assert_awaited_once_with(42)


# ===========================================================================
# execute (end-to-end)
# ===========================================================================


class TestExecute:
    """Tests for the public execute method."""

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"code written", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor.execute("write code", {"task_id": "t-1"})

        assert result.success is True
        assert result.stdout == "code written"

    @pytest.mark.asyncio
    async def test_execute_uses_context_workspace(self) -> None:
        executor = _make_executor(workspace_dir="/default")
        mock_proc = _mock_process(stdout=b"ok", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await executor.execute("prompt", {"task_id": "t-2", "workspace_dir": "/custom"})

        # Verify cwd was set to the context workspace_dir
        _, kwargs = mock_exec.call_args
        assert kwargs["cwd"] == "/custom"

    @pytest.mark.asyncio
    async def test_execute_defaults_workspace(self) -> None:
        executor = _make_executor(workspace_dir="/default-ws")
        mock_proc = _mock_process(stdout=b"ok", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await executor.execute("prompt", {"task_id": "t-3"})

        _, kwargs = mock_exec.call_args
        assert kwargs["cwd"] == "/default-ws"

    @pytest.mark.asyncio
    async def test_execute_defaults_task_id_unknown(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"ok", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            # No task_id in context
            result = await executor.execute("prompt", {})

        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_failure(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"", stderr=b"compilation error", returncode=2)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor.execute("bad code", {"task_id": "t-4"})

        assert result.success is False
        assert result.error_message == "compilation error"


# ===========================================================================
# review (end-to-end)
# ===========================================================================


class TestReview:
    """Tests for the public review method."""

    @pytest.mark.asyncio
    async def test_review_pass(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"All looks good.\nVERDICT: PASS", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("backend.src.core.executor.claude_code.get_prompt", return_value="review: {task_prompt}"):
            result = await executor.review("check this", {"task_id": "t-r1"})

        assert isinstance(result, ReviewResult)
        assert result.passed is True
        assert "PASS" in result.feedback

    @pytest.mark.asyncio
    async def test_review_fail(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"Issues found.\nVERDICT: FAIL", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("backend.src.core.executor.claude_code.get_prompt", return_value="review: {task_prompt}"):
            result = await executor.review("check this", {"task_id": "t-r2"})

        assert result.passed is False
        assert "FAIL" in result.feedback

    @pytest.mark.asyncio
    async def test_review_execution_failure(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"", stderr=b"crash", returncode=1)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("backend.src.core.executor.claude_code.get_prompt", return_value="review: {task_prompt}"):
            result = await executor.review("check this", {"task_id": "t-r3"})

        assert result.passed is False
        assert result.error_message == "crash"

    @pytest.mark.asyncio
    async def test_review_no_verdict_defaults_fail(self) -> None:
        executor = _make_executor()
        mock_proc = _mock_process(stdout=b"Some feedback with no verdict line", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("backend.src.core.executor.claude_code.get_prompt", return_value="review: {task_prompt}"):
            result = await executor.review("check this", {"task_id": "t-r4"})

        assert result.passed is False


# ===========================================================================
# _parse_review_verdict
# ===========================================================================


class TestParseReviewVerdict:
    """Tests for _parse_review_verdict static method."""

    def test_verdict_pass(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("some text\nVERDICT: PASS") is True

    def test_verdict_fail(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("some text\nVERDICT: FAIL") is False

    def test_result_pass(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("RESULT: PASS") is True

    def test_result_fail(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("RESULT: FAIL") is False

    def test_bare_pass(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("feedback\nPASS") is True

    def test_bare_fail(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("feedback\nFAIL") is False

    def test_no_verdict_defaults_false(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("just some text") is False

    def test_empty_string(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("") is False

    def test_markdown_formatting_stripped(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("**VERDICT: PASS**") is True

    def test_backtick_formatting_stripped(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("`VERDICT: FAIL`") is False

    def test_hash_formatting_stripped(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("### VERDICT: PASS") is True

    def test_last_line_takes_priority(self) -> None:
        output = "VERDICT: FAIL\nVERDICT: PASS"
        # reversed iteration means last line is checked first
        assert ClaudeCodeExecutor._parse_review_verdict(output) is True

    def test_bare_keyword_fallback_only_if_no_verdict_prefix(self) -> None:
        # When VERDICT/RESULT lines exist, the bare fallback is not used
        output = "FAIL\nVERDICT: PASS"
        assert ClaudeCodeExecutor._parse_review_verdict(output) is True

    def test_pass_with_extra_text(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("VERDICT: PASS - all good") is True

    def test_fail_with_extra_text(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("VERDICT: FAIL - issues found") is False

    def test_verdict_without_colon(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("VERDICT PASS") is True

    def test_verdict_with_underscores_and_dashes(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("__VERDICT: PASS__") is True

    def test_verdict_in_blockquote(self) -> None:
        assert ClaudeCodeExecutor._parse_review_verdict("> VERDICT: PASS") is True

    def test_verdict_mid_output_fallback(self) -> None:
        """Verdict found via fallback search when not at line start after stripping."""
        output = "Some review\nAll checks passed\nFinal VERDICT: PASS\nEnd."
        assert ClaudeCodeExecutor._parse_review_verdict(output) is True


# -- create_executor factory --------------------------------------------------


def test_create_executor_claude_code():
    """create_executor('claude-code') returns ClaudeCodeExecutor."""
    from backend.src.core.executor import create_executor

    executor = create_executor("claude-code")
    assert isinstance(executor, ClaudeCodeExecutor)


def test_create_executor_unknown_raises():
    """create_executor with unknown type raises ValueError."""
    from backend.src.core.executor import create_executor

    with pytest.raises(ValueError, match="Unknown executor type"):
        create_executor("not-a-real-executor")
