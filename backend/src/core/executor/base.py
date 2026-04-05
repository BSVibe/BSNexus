import enum
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


class ExecutorCapability(str, enum.Enum):
    coding = "coding"
    writing = "writing"
    analysis = "analysis"
    marketing = "marketing"
    research = "research"
    general = "general"


@dataclass
class ExecutorInfo:
    name: str
    capabilities: list[ExecutorCapability]
    requires_local: bool = False
    requires_workspace: bool = False
    description: str = ""


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
