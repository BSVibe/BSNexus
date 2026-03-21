from backend.src.core.executor.base import BaseExecutor, ExecutionResult, ReviewResult
from backend.src.core.executor.claude_code import ClaudeCodeExecutor

__all__ = ["BaseExecutor", "ClaudeCodeExecutor", "ExecutionResult", "ReviewResult"]


def create_executor(executor_type: str = "claude-code") -> BaseExecutor:
    """Create an executor instance by type."""
    if executor_type == "claude-code":
        return ClaudeCodeExecutor()
    raise ValueError(f"Unknown executor type: {executor_type}")
