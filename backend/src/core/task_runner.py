from __future__ import annotations

import json
import structlog
import time
from dataclasses import dataclass

from backend.src.core.executor.base import ExecutorProtocol
from backend.src.core.executor.registry import ExecutorRegistry
from backend.src.core.git_ops import GitOps
from backend.src.models import Task

logger = structlog.get_logger(__name__)

_ENVIRONMENT_EXCEPTIONS = (
    FileNotFoundError,
    PermissionError,
    OSError,
    ConnectionError,
    TimeoutError,
    UnicodeEncodeError,
    UnicodeDecodeError,
)


@dataclass
class TaskExecutionResult:
    success: bool
    error_message: str = ""
    error_category: str = ""


@dataclass
class TaskReviewResult:
    passed: bool
    feedback: str = ""
    commit_hash: str = ""
    error_message: str = ""
    error_category: str = ""


class LocalTaskRunner:
    """Executes tasks and QA reviews locally on the backend server."""

    def __init__(
        self,
        executor_name: str = "claude_code",
        *,
        executor: ExecutorProtocol | None = None,
    ) -> None:
        self._default_executor_name = executor_name
        self._executor = executor

    def _resolve_executor(self, executor_type: str) -> ExecutorProtocol:
        """Resolve an executor: try executor_type from registry, fall back to default name."""
        if self._executor is not None:
            return self._executor
        registry = ExecutorRegistry()
        try:
            return registry.get(executor_type)
        except KeyError:
            if executor_type != self._default_executor_name:
                return registry.get(self._default_executor_name)
            raise

    @staticmethod
    def _classify_exception(exc: Exception) -> str:
        """Classify an exception into an error category."""
        if isinstance(exc, _ENVIRONMENT_EXCEPTIONS):
            return "environment"
        return ""

    @staticmethod
    def _extract_prompt(data: str | dict | None, fallback: str = "") -> str:
        """Extract prompt string from a field (may be JSON-encoded or plain string)."""
        if data is None:
            return fallback
        if isinstance(data, dict):
            return data.get("prompt", str(data))
        raw = str(data)
        try:
            parsed = json.loads(raw)
            return parsed.get("prompt", raw) if isinstance(parsed, dict) else raw
        except (json.JSONDecodeError, TypeError):
            return raw

    async def execute_task(self, task: Task, repo_path: str) -> TaskExecutionResult:
        """Execute a task: git setup, run executor, return result."""
        task_id = str(task.id)
        branch_name = task.branch_name or ""
        title = task.title or ""
        executor_type = getattr(task, "executor_type", self._default_executor_name) or self._default_executor_name

        logger.info(">>> TASK START task_id=%s title='%s' executor_type=%s", task_id, title, executor_type)

        executor = self._resolve_executor(executor_type)
        prompt = self._extract_prompt(task.worker_prompt)

        # Inject retry feedback from previous failed attempt
        last_feedback_entry = task.qa_feedback_history[-1] if task.qa_feedback_history else None
        retry_feedback = ""
        if isinstance(last_feedback_entry, dict):
            retry_feedback = last_feedback_entry.get("feedback") or last_feedback_entry.get("error") or ""
        elif last_feedback_entry:
            retry_feedback = str(last_feedback_entry)
        if retry_feedback and task.retry_count > 0:
            prompt = (
                f"PREVIOUS ATTEMPT FAILED (attempt {task.retry_count}).\n"
                f"Feedback from previous attempt:\n{retry_feedback}\n\n"
                f"Please fix the issues identified above and complete the task.\n\n"
                f"Original task:\n{prompt}"
            )

        t0 = time.monotonic()
        try:
            git_ops = None
            if repo_path:
                git_ops = GitOps(repo_path)
                await git_ops.ensure_repo()
                if branch_name:
                    await git_ops.ensure_branch(branch_name)
                    logger.info("    git: checked out branch %s", branch_name)

            context: dict = {"task_id": task_id}
            if repo_path:
                context["workspace_dir"] = repo_path

            result = await executor.execute(prompt, context)
            elapsed = time.monotonic() - t0

            if git_ops:
                try:
                    status = await git_ops.get_status()
                    logger.info("    git status after exec: %s", status[:300] if status else "(clean)")
                except Exception as e:
                    logger.debug("    git status failed: %s", e)

            if result.success:
                logger.info("<<< TASK DONE task_id=%s success=true elapsed=%.1fs", task_id, elapsed)
                return TaskExecutionResult(success=True)
            else:
                logger.warning(
                    "<<< TASK DONE task_id=%s success=false elapsed=%.1fs error=%s",
                    task_id,
                    elapsed,
                    result.error_message or "(unknown)",
                )
                return TaskExecutionResult(
                    success=False,
                    error_message=result.error_message or "",
                    error_category=result.error_category,
                )
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error("<<< TASK FAILED task_id=%s elapsed=%.1fs error=%s", task_id, elapsed, e)
            return TaskExecutionResult(
                success=False,
                error_message=str(e),
                error_category=self._classify_exception(e),
            )

    async def review_task(self, task: Task, repo_path: str) -> TaskReviewResult:
        """Run QA review: new session reviews code, commit on pass."""
        task_id = str(task.id)
        branch_name = task.branch_name or ""
        title = task.title or ""
        executor_type = getattr(task, "executor_type", self._default_executor_name) or self._default_executor_name

        logger.info(">>> QA START task_id=%s executor_type=%s", task_id, executor_type)

        executor = self._resolve_executor(executor_type)
        prompt = self._extract_prompt(task.qa_prompt)

        t0 = time.monotonic()
        try:
            git_ops = None
            if repo_path:
                git_ops = GitOps(repo_path)
                await git_ops.ensure_repo()
                if branch_name:
                    await git_ops.ensure_branch(branch_name)

            context: dict = {"task_id": task_id}
            if repo_path:
                context["workspace_dir"] = repo_path

            result = await executor.review(prompt, context)
            elapsed = time.monotonic() - t0

            commit_hash = ""
            if result.passed and git_ops and branch_name:
                try:
                    commit_hash = await git_ops.commit_task(task_id, title, branch_name)
                    if commit_hash:
                        logger.info("    git: committed after QA pass %s", commit_hash[:8])
                    else:
                        logger.warning("    qa: passed but no file changes to commit")
                except RuntimeError:
                    logger.warning("    git: commit after QA pass failed", exc_info=True)

            if result.passed:
                logger.info("<<< QA DONE task_id=%s passed=true elapsed=%.1fs", task_id, elapsed)
            else:
                feedback_preview = (result.feedback[:120].replace("\n", " ")) if result.feedback else ""
                logger.warning(
                    "<<< QA DONE task_id=%s passed=false elapsed=%.1fs feedback=%s", task_id, elapsed, feedback_preview
                )

            return TaskReviewResult(
                passed=result.passed,
                feedback=result.feedback,
                commit_hash=commit_hash,
                error_message=result.error_message or "",
                error_category=result.error_category,
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error("<<< QA FAILED task_id=%s elapsed=%.1fs error=%s", task_id, elapsed, e)
            return TaskReviewResult(
                passed=False,
                error_message=str(e),
                error_category=self._classify_exception(e),
            )
