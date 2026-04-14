"""Tool system base types and abstract Tool class.

All LLM tool_use interactions flow through these types:
- ToolDefinition: JSON Schema sent to the LLM
- ToolCall: what the LLM wants to invoke
- ToolResult: what the tool returns
- ToolContext: per-request state shared by all tools
- Tool: abstract base for implementing tools
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from backend.src.queue.streams import RedisStreamManager


@dataclass(frozen=True)
class ToolDefinition:
    """Schema sent to the LLM so it knows how to call the tool."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    """Result of executing a ToolCall."""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class ToolContext:
    """Per-request context passed to every tool execution.

    Created once per agent turn. Tools open short-lived DB sessions
    via ``db_session_factory`` — no long-held connections.
    """

    project_id: uuid.UUID
    workspace_path: Path
    workspace_type: str  # "server_managed" | "local_import" | "github_connected"
    agent_id: uuid.UUID
    agent_name: str
    tenant_id: uuid.UUID
    db_session_factory: Callable[..., Any]  # async context manager → AsyncSession
    redis: Any | None = None
    stream_manager: RedisStreamManager | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    tasks_created_this_turn: int = 0
    max_tasks_per_turn: int = 10


class Tool(ABC):
    """Abstract base for a BSNexus tool.

    Subclasses implement ``execute()`` which receives parsed input
    from the LLM and a ``ToolContext`` for accessing workspace / DB.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier used in LLM tool_use calls."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description shown to the LLM."""
        ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema for the tool's input parameters."""
        ...

    @abstractmethod
    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        """Run the tool and return a text result for the LLM.

        Implementations should:
        - Open short-lived DB sessions via ``ctx.db_session_factory``
        - Access files via ``ctx.workspace_path``
        - Raise ``ToolExecutionError`` on expected failures
        - Never hold resources across awaits
        """
        ...

    def to_definition(self) -> ToolDefinition:
        """Convert to the schema format sent to the LLM."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


class ToolExecutionError(Exception):
    """Raised by tools for expected, user-visible errors."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
