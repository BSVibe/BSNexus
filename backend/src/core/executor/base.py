from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class ExecutionResult:
    success: bool
    output_path: str | None = None
    error_message: str | None = None
    stdout: str = ""
    stderr: str = ""
    error_category: Literal["environment", "tool", ""] = ""


@dataclass
class ReviewResult:
    passed: bool
    feedback: str = ""
    error_message: str | None = None
    error_category: Literal["environment", "tool", ""] = ""


@runtime_checkable
class ExecutorProtocol(Protocol):
    """Task executor interface using structural subtyping."""

    async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult: ...

    async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult: ...

    def supported_task_types(self) -> list[str]: ...


# Backward-compatible alias
BaseExecutor = ExecutorProtocol
