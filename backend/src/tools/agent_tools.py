"""Agent-to-tool mapping — determines which tools each agent can use.

Maps agent capabilities (from Agent.capabilities list) to tool names.
Universal tools are available to all agents regardless of capabilities.
"""

from __future__ import annotations

from backend.src.tools.base import Tool
from backend.src.tools.design_tools import CreateScreenTool, ModifyScreenTool
from backend.src.tools.plan_tools import (
    ClaimTaskTool,
    CompleteTaskTool,
    CreatePhaseTool,
    CreateTaskTool,
    ListTasksTool,
    RecordDecisionTool,
    SetGoalTool,
)
from backend.src.tools.workspace_tools import FileReadTool, FileWriteTool, ListFilesTool

# ── Tool instances (singletons) ──────────────────────────────────────

_TOOL_INSTANCES: dict[str, Tool] = {}


def _ensure_instances() -> dict[str, Tool]:
    """Lazily create tool instances."""
    if not _TOOL_INSTANCES:
        tools: list[Tool] = [
            # Workspace
            FileReadTool(),
            FileWriteTool(),
            ListFilesTool(),
            # Plan management
            CreateTaskTool(),
            ClaimTaskTool(),
            CompleteTaskTool(),
            ListTasksTool(),
            CreatePhaseTool(),
            SetGoalTool(),
            RecordDecisionTool(),
            # Design
            CreateScreenTool(),
            ModifyScreenTool(),
        ]
        for t in tools:
            _TOOL_INSTANCES[t.name] = t
    return _TOOL_INSTANCES


# ── Capability → tool name mapping ───────────────────────────────────

CAPABILITY_TOOLS: dict[str, list[str]] = {
    "plan": [
        "create_phase",
        "create_task",
        "complete_task",
        "claim_task",
        "set_goal",
        "record_decision",
        "list_tasks",
        "file_read",
        "list_files",
    ],
    "design": [
        "create_screen",
        "modify_screen",
        "file_read",
        "file_write",
        "list_files",
        "create_task",
        "complete_task",
        "list_tasks",
    ],
    "coding": [
        "file_read",
        "file_write",
        "list_files",
        "create_task",
        "claim_task",
        "complete_task",
        "list_tasks",
    ],
    "analyze": [
        "file_read",
        "list_files",
        "create_task",
        "complete_task",
        "record_decision",
        "list_tasks",
    ],
    "architect": [
        "file_read",
        "file_write",
        "list_files",
        "create_task",
        "create_phase",
        "complete_task",
        "record_decision",
        "list_tasks",
    ],
    "marketing": [
        "file_read",
        "file_write",
        "list_files",
        "create_task",
        "complete_task",
        "list_tasks",
    ],
}

# Tools available to every agent regardless of capabilities.
UNIVERSAL_TOOLS: list[str] = ["file_read", "list_files", "list_tasks"]


def get_tools_for_agent(capabilities: list[str] | None) -> list[Tool]:
    """Return tool instances available to an agent based on its capabilities.

    Args:
        capabilities: Agent.capabilities list (e.g., ["plan", "analyze"]).
            If None or empty, only universal tools are returned.
    """
    instances = _ensure_instances()

    tool_names: set[str] = set(UNIVERSAL_TOOLS)
    for cap in (capabilities or []):
        key = (cap or "").strip().lower()
        for name in CAPABILITY_TOOLS.get(key, []):
            tool_names.add(name)

    return [instances[name] for name in sorted(tool_names) if name in instances]


def get_tools_for_mode(mode: str, capabilities: list[str] | None) -> list[Tool]:
    """Return tools filtered by workflow mode.

    Active mode: planning tools (create_task, create_phase, etc.)
    Passive mode: execution tools (claim_task, complete_task, file_write, etc.)
    """
    instances = _ensure_instances()

    if mode == "passive":
        tool_names: set[str] = {
            "claim_task", "complete_task",
            "file_read", "file_write", "list_files", "list_tasks",
        }
        # Add capability-specific execution tools
        caps = {(c or "").strip().lower() for c in (capabilities or [])}
        if "design" in caps:
            tool_names |= {"create_screen", "modify_screen"}
    else:  # active
        tool_names = {
            "create_task", "create_phase", "list_tasks",
            "set_goal", "record_decision",
            "file_read", "list_files",
        }

    return [instances[name] for name in sorted(tool_names) if name in instances]
