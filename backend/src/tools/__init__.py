"""BSNexus Tool System — tool_use based agent capabilities.

Provides the abstraction layer for LLM tool_use integration:
- Tool base class and data types
- ToolHandler for executing tool calls from LLM responses
- ToolContext for per-request workspace/DB access
- Built-in tools for workspace, plan management, and design
"""

from backend.src.tools.base import (
    Tool,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from backend.src.tools.handler import ToolHandler

__all__ = [
    "Tool",
    "ToolCall",
    "ToolContext",
    "ToolDefinition",
    "ToolHandler",
    "ToolResult",
]
