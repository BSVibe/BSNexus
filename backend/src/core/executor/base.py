from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal, Optional


@dataclass
class ExecutionResult:
    success: bool
    output_path: Optional[str] = None
    error_message: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    error_category: Literal["environment", "tool", ""] = ""


@dataclass
class ReviewResult:
    passed: bool
    feedback: str = ""
    error_message: Optional[str] = None
    error_category: Literal["environment", "tool", ""] = ""


class BaseExecutor(ABC):
    """Task executor interface."""

    @abstractmethod
    async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
        """Execute a coding task."""

    @abstractmethod
    async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult:
        """Execute a code review."""
