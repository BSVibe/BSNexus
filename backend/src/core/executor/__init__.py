from backend.src.core.executor.base import BaseExecutor, ExecutionResult, ExecutorProtocol, ReviewResult
from backend.src.core.executor.claude_code import ClaudeCodeExecutor
from backend.src.core.executor.registry import ExecutorRegistry

__all__ = [
    "BaseExecutor",
    "ClaudeCodeExecutor",
    "ExecutionResult",
    "ExecutorProtocol",
    "ExecutorRegistry",
    "ReviewResult",
]

# Register built-in executors
_registry = ExecutorRegistry()
if "claude_code" not in _registry.list_available():
    _registry.register("claude_code", ClaudeCodeExecutor)


def create_executor(executor_type: str = "claude_code") -> ExecutorProtocol:
    """Create an executor instance by type, resolved via ExecutorRegistry."""
    return _registry.get(executor_type)
