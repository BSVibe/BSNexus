"""Comprehensive tests for backend.src.core.task_runner module."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.executor.base import BaseExecutor, ExecutionResult, ReviewResult
from backend.src.core.task_runner import LocalTaskRunner, TaskExecutionResult, TaskReviewResult


# ── Helpers ───────────────────────────────────────────────────────────


def make_task(
    *,
    title: str = "Test task",
    branch_name: str | None = "feature/test",
    worker_prompt: dict | str | None = None,
    qa_prompt: dict | str | None = None,
    retry_count: int = 0,
    qa_feedback_history: list | None = None,
) -> MagicMock:
    """Create a mock Task with sensible defaults."""
    task = MagicMock()
    task.id = uuid.uuid4()
    task.title = title
    task.branch_name = branch_name
    task.worker_prompt = worker_prompt
    task.qa_prompt = qa_prompt
    task.retry_count = retry_count
    task.qa_feedback_history = qa_feedback_history or []
    return task


# ── _classify_exception tests ─────────────────────────────────────────


class TestClassifyException:
    def test_classify_exception_environment_errors(self) -> None:
        runner = LocalTaskRunner(executor=AsyncMock(spec=BaseExecutor))
        for exc in (FileNotFoundError("x"), PermissionError("x"), OSError("x")):
            assert runner._classify_exception(exc) == "environment"

    def test_classify_exception_non_environment(self) -> None:
        runner = LocalTaskRunner(executor=AsyncMock(spec=BaseExecutor))
        for exc in (ValueError("x"), RuntimeError("x"), KeyError("x")):
            assert runner._classify_exception(exc) == ""


# ── _extract_prompt tests ─────────────────────────────────────────────


class TestExtractPrompt:
    def test_extract_prompt_from_dict(self) -> None:
        assert LocalTaskRunner._extract_prompt({"prompt": "do it"}) == "do it"

    def test_extract_prompt_from_json_string(self) -> None:
        assert LocalTaskRunner._extract_prompt('{"prompt": "do it"}') == "do it"

    def test_extract_prompt_from_plain_string(self) -> None:
        assert LocalTaskRunner._extract_prompt("just do it") == "just do it"

    def test_extract_prompt_none(self) -> None:
        assert LocalTaskRunner._extract_prompt(None) == ""
        assert LocalTaskRunner._extract_prompt(None, fallback="default") == "default"

    def test_extract_prompt_json_string_without_prompt_key(self) -> None:
        result = LocalTaskRunner._extract_prompt('{"other": "value"}')
        assert result == '{"other": "value"}'

    def test_extract_prompt_dict_without_prompt_key(self) -> None:
        result = LocalTaskRunner._extract_prompt({"other": "value"})
        assert "other" in result

    def test_extract_prompt_non_dict_json(self) -> None:
        # JSON that parses to a list, not a dict
        assert LocalTaskRunner._extract_prompt("[1, 2, 3]") == "[1, 2, 3]"


# ── execute_task tests ────────────────────────────────────────────────


class TestExecuteTask:
    @pytest.mark.asyncio
    async def test_execute_task_success(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt={"prompt": "write code"})

        result = await runner.execute_task(task, repo_path="")

        assert isinstance(result, TaskExecutionResult)
        assert result.success is True
        assert result.error_message == ""
        mock_executor.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_task_failure(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(
            success=False, error_message="syntax error", error_category="tool"
        )

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt="write code")

        result = await runner.execute_task(task, repo_path="")

        assert result.success is False
        assert result.error_message == "syntax error"
        assert result.error_category == "tool"

    @pytest.mark.asyncio
    async def test_execute_task_exception(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.side_effect = FileNotFoundError("no such file")

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt="write code")

        result = await runner.execute_task(task, repo_path="")

        assert result.success is False
        assert "no such file" in result.error_message
        assert result.error_category == "environment"

    @pytest.mark.asyncio
    async def test_execute_task_exception_non_environment(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.side_effect = ValueError("bad value")

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt="write code")

        result = await runner.execute_task(task, repo_path="")

        assert result.success is False
        assert "bad value" in result.error_message
        assert result.error_category == ""

    @pytest.mark.asyncio
    async def test_execute_task_with_git_setup(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        mock_git = AsyncMock()
        mock_git.get_status.return_value = "M file.py"

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt="code", branch_name="feature/x")

        with patch("backend.src.core.task_runner.GitOps", return_value=mock_git):
            result = await runner.execute_task(task, repo_path="/tmp/repo")

        assert result.success is True
        mock_git.ensure_repo.assert_awaited_once()
        mock_git.ensure_branch.assert_awaited_once_with("feature/x")
        mock_git.get_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_task_without_repo_path(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt="code")

        with patch("backend.src.core.task_runner.GitOps") as git_cls:
            result = await runner.execute_task(task, repo_path="")

        assert result.success is True
        git_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_task_without_branch_name(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        mock_git = AsyncMock()
        mock_git.get_status.return_value = ""

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt="code", branch_name=None)

        with patch("backend.src.core.task_runner.GitOps", return_value=mock_git):
            result = await runner.execute_task(task, repo_path="/tmp/repo")

        assert result.success is True
        mock_git.ensure_repo.assert_awaited_once()
        mock_git.ensure_branch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_task_injects_retry_feedback(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(
            worker_prompt={"prompt": "original task"},
            retry_count=2,
            qa_feedback_history=[
                {"feedback": "tests failing"},
                {"feedback": "still broken"},
            ],
        )

        await runner.execute_task(task, repo_path="")

        prompt_arg = mock_executor.execute.call_args[0][0]
        assert "PREVIOUS ATTEMPT FAILED (attempt 2)" in prompt_arg
        assert "still broken" in prompt_arg
        assert "original task" in prompt_arg

    @pytest.mark.asyncio
    async def test_execute_task_no_feedback_on_first_attempt(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(
            worker_prompt={"prompt": "original task"},
            retry_count=0,
            qa_feedback_history=[],
        )

        await runner.execute_task(task, repo_path="")

        prompt_arg = mock_executor.execute.call_args[0][0]
        assert "PREVIOUS ATTEMPT FAILED" not in prompt_arg
        assert prompt_arg == "original task"

    @pytest.mark.asyncio
    async def test_execute_task_retry_feedback_string_entry(self) -> None:
        """qa_feedback_history entry is a plain string, not a dict."""
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(
            worker_prompt={"prompt": "original"},
            retry_count=1,
            qa_feedback_history=["plain string feedback"],
        )

        await runner.execute_task(task, repo_path="")

        prompt_arg = mock_executor.execute.call_args[0][0]
        assert "plain string feedback" in prompt_arg
        assert "PREVIOUS ATTEMPT FAILED" in prompt_arg

    @pytest.mark.asyncio
    async def test_execute_task_git_status_fails_gracefully(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        mock_git = AsyncMock()
        mock_git.get_status.side_effect = RuntimeError("git status failed")

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt="code", branch_name="feature/x")

        with patch("backend.src.core.task_runner.GitOps", return_value=mock_git):
            result = await runner.execute_task(task, repo_path="/tmp/repo")

        # Should still succeed — git status failure is non-fatal
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_task_context_includes_workspace_dir(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        mock_git = AsyncMock()
        mock_git.get_status.return_value = ""

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(worker_prompt="code")

        with patch("backend.src.core.task_runner.GitOps", return_value=mock_git):
            await runner.execute_task(task, repo_path="/tmp/repo")

        context_arg = mock_executor.execute.call_args[0][1]
        assert context_arg["workspace_dir"] == "/tmp/repo"
        assert "task_id" in context_arg


# ── review_task tests ─────────────────────────────────────────────────


class TestReviewTask:
    @pytest.mark.asyncio
    async def test_review_task_pass_with_commit(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.return_value = ReviewResult(
            passed=True, feedback="", error_message=None, error_category=""
        )

        mock_git = AsyncMock()
        mock_git.commit_task.return_value = "abc123def456"

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt={"prompt": "review code"}, branch_name="feature/x")

        with patch("backend.src.core.task_runner.GitOps", return_value=mock_git):
            result = await runner.review_task(task, repo_path="/tmp/repo")

        assert isinstance(result, TaskReviewResult)
        assert result.passed is True
        assert result.commit_hash == "abc123def456"
        assert result.error_message == ""
        mock_git.commit_task.assert_awaited_once_with(str(task.id), task.title, "feature/x")

    @pytest.mark.asyncio
    async def test_review_task_pass_no_changes(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.return_value = ReviewResult(
            passed=True, feedback="", error_message=None, error_category=""
        )

        mock_git = AsyncMock()
        mock_git.commit_task.return_value = ""  # no changes

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt="review", branch_name="feature/x")

        with patch("backend.src.core.task_runner.GitOps", return_value=mock_git):
            result = await runner.review_task(task, repo_path="/tmp/repo")

        assert result.passed is True
        assert result.commit_hash == ""

    @pytest.mark.asyncio
    async def test_review_task_fail(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.return_value = ReviewResult(
            passed=False, feedback="Missing error handling in parse()", error_message=None, error_category=""
        )

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt="review code")

        result = await runner.review_task(task, repo_path="")

        assert result.passed is False
        assert "Missing error handling" in result.feedback
        assert result.commit_hash == ""

    @pytest.mark.asyncio
    async def test_review_task_exception(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.side_effect = PermissionError("access denied")

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt="review")

        result = await runner.review_task(task, repo_path="")

        assert result.passed is False
        assert "access denied" in result.error_message
        assert result.error_category == "environment"

    @pytest.mark.asyncio
    async def test_review_task_commit_failure(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.return_value = ReviewResult(
            passed=True, feedback="", error_message=None, error_category=""
        )

        mock_git = AsyncMock()
        mock_git.commit_task.side_effect = RuntimeError("git commit failed")

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt="review", branch_name="feature/x")

        with patch("backend.src.core.task_runner.GitOps", return_value=mock_git):
            result = await runner.review_task(task, repo_path="/tmp/repo")

        # Review passed but commit failed — result should still be passed with empty hash
        assert result.passed is True
        assert result.commit_hash == ""

    @pytest.mark.asyncio
    async def test_review_task_without_repo_path(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.return_value = ReviewResult(
            passed=True, feedback="", error_message=None, error_category=""
        )

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt="review", branch_name="feature/x")

        with patch("backend.src.core.task_runner.GitOps") as git_cls:
            result = await runner.review_task(task, repo_path="")

        assert result.passed is True
        assert result.commit_hash == ""
        git_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_review_task_without_branch_name(self) -> None:
        """Review passes but no branch_name means no commit attempt."""
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.return_value = ReviewResult(
            passed=True, feedback="", error_message=None, error_category=""
        )

        mock_git = AsyncMock()

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt="review", branch_name=None)

        with patch("backend.src.core.task_runner.GitOps", return_value=mock_git):
            result = await runner.review_task(task, repo_path="/tmp/repo")

        assert result.passed is True
        assert result.commit_hash == ""
        mock_git.commit_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_review_task_fail_with_error_message(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.return_value = ReviewResult(
            passed=False, feedback="bad code", error_message="validation failed", error_category="tool"
        )

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt="review")

        result = await runner.review_task(task, repo_path="")

        assert result.passed is False
        assert result.error_message == "validation failed"
        assert result.error_category == "tool"
        assert result.feedback == "bad code"

    @pytest.mark.asyncio
    async def test_review_task_exception_non_environment(self) -> None:
        mock_executor = AsyncMock(spec=BaseExecutor)
        mock_executor.review.side_effect = ValueError("bad input")

        runner = LocalTaskRunner(executor=mock_executor)
        task = make_task(qa_prompt="review")

        result = await runner.review_task(task, repo_path="")

        assert result.passed is False
        assert "bad input" in result.error_message
        assert result.error_category == ""
