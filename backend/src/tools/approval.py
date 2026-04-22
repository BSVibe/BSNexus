"""Approval middleware — intercepts tool calls that require approval.

Sits between ToolHandler and actual tool execution. When a tool call
matches an approval rule (from .bsnexus/settings.json), the middleware
stores a PlanProposal instead of executing the tool.

Separated from Tool implementations so tools don't know about approval.
"""

from __future__ import annotations

import json

import structlog

from backend.src.tools.base import ToolCall, ToolContext, ToolResult

logger = structlog.get_logger(__name__)

# Tool name → approval setting key mapping.
_TOOL_APPROVAL_KEYS: dict[str, str] = {
    "create_phase": "phase_creation",
    "create_task": "task_creation",
}


class ApprovalMiddleware:
    """Check approval settings before executing tools.

    If a tool requires approval, stores a PlanProposal and returns
    a "proposal created" result instead of executing the tool.
    """

    async def intercept(self, call: ToolCall, ctx: ToolContext) -> ToolResult | None:
        """Return a ToolResult if the call is intercepted, None to proceed normally."""
        approval_key = _TOOL_APPROVAL_KEYS.get(call.name)
        if not approval_key:
            return None  # This tool doesn't have approval rules

        settings = self._read_settings(ctx)
        level = settings.get(approval_key, "auto_approve")

        if level == "auto_approve":
            return None  # Proceed with normal execution

        # require_approval: store as PlanProposal
        return await self._store_proposal(call, ctx)

    def _read_settings(self, ctx: ToolContext) -> dict[str, str]:
        """Read approval settings from .bsnexus/settings.json."""
        from backend.src.core.harness import read_approval_settings

        workspace_dir = str(ctx.workspace_path) if ctx.workspace_path.is_dir() else None
        return read_approval_settings(workspace_dir)

    async def _store_proposal(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """Store a PlanProposal in the database."""
        from backend.src.models import PlanProposal, ProposalType

        # Determine proposal type from tool name
        if call.name == "create_phase":
            proposal_type = ProposalType.phase
            title = call.input.get("name", "Untitled Phase")
        elif call.name == "create_task":
            proposal_type = ProposalType.task
            title = call.input.get("title", "Untitled Task")
        else:
            # Shouldn't reach here, but handle gracefully
            return None  # type: ignore[return-value]

        async with ctx.db_session_factory() as db:
            proposal = PlanProposal(
                project_id=ctx.project_id,
                tenant_id=ctx.tenant_id,
                proposer_agent_id=ctx.agent_id,
                proposer_agent_name=ctx.agent_name,
                proposal_type=proposal_type,
                payload=call.input,
            )
            db.add(proposal)
            await db.commit()

            logger.info(
                "proposal_created_via_tool",
                proposal_id=str(proposal.id),
                proposal_type=proposal_type.value,
                title=title,
                agent=ctx.agent_name,
            )

            return ToolResult(
                tool_call_id=call.id,
                content=json.dumps({
                    "status": "proposal_created",
                    "proposal_id": str(proposal.id),
                    "proposal_type": proposal_type.value,
                    "title": title,
                    "message": f"{proposal_type.value.title()} 제안이 등록되었습니다. 승인 대기 중.",
                }),
            )
