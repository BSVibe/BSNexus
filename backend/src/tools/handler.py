"""ToolHandler — bridges LLM tool_calls to Tool.execute().

Created per-request with a curated tool list and shared ToolContext.
The ApprovalMiddleware is checked before each tool execution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.src.tools.base import Tool, ToolCall, ToolContext, ToolExecutionError, ToolResult

if TYPE_CHECKING:
    from backend.src.tools.approval import ApprovalMiddleware

logger = structlog.get_logger(__name__)


class ToolHandler:
    """Execute tool calls from LLM responses.

    Instantiated per agent turn with the tools available to that agent.
    """

    def __init__(
        self,
        tools: list[Tool],
        context: ToolContext,
        *,
        approval: "ApprovalMiddleware | None" = None,
    ) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}
        self._context = context
        self._approval = approval

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    async def execute_batch(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """Execute multiple tool calls sequentially.

        Sequential (not parallel) because tools may have side effects
        that depend on order (e.g., create_phase then create_task).
        """
        results: list[ToolResult] = []
        for call in tool_calls:
            result = await self.execute_single(call)
            results.append(result)
        return results

    async def execute_single(self, call: ToolCall) -> ToolResult:
        """Execute one tool call with approval check and error handling."""
        tool = self._tools.get(call.name)
        if not tool:
            logger.warning("unknown_tool_call", tool=call.name, agent=self._context.agent_name)
            return ToolResult(
                tool_call_id=call.id,
                content=f"Unknown tool: {call.name}. Available tools: {', '.join(self.tool_names)}",
                is_error=True,
            )

        # Approval middleware intercept
        if self._approval:
            intercepted = await self._approval.intercept(call, self._context)
            if intercepted is not None:
                logger.info(
                    "tool_intercepted_by_approval",
                    tool=call.name,
                    agent=self._context.agent_name,
                )
                return intercepted

        # Execute the tool
        try:
            output = await tool.execute(call.input, self._context)
            logger.info(
                "tool_executed",
                tool=call.name,
                agent=self._context.agent_name,
                output_len=len(output),
            )
            return ToolResult(tool_call_id=call.id, content=output)
        except ToolExecutionError as e:
            logger.warning(
                "tool_execution_error",
                tool=call.name,
                agent=self._context.agent_name,
                error=e.message,
            )
            return ToolResult(tool_call_id=call.id, content=e.message, is_error=True)
        except Exception as e:
            logger.error(
                "tool_unexpected_error",
                tool=call.name,
                agent=self._context.agent_name,
                error=str(e),
                exc_info=True,
            )
            return ToolResult(
                tool_call_id=call.id,
                content=f"Internal error executing {call.name}: {type(e).__name__}",
                is_error=True,
            )
