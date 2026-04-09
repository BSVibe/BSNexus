"""BSNexus domain models.

Re-exports everything from the legacy module for backward compatibility.
New models are defined in separate submodules.
"""

# Legacy models (all existing code imports from here)
from backend.src.models._legacy import (
    DesignMessage,
    DesignSession,
    DesignSessionStatus,
    MessageRole,
    MessageType,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Setting,
    WorkspaceType,
    SuggestionStatus,
    Task,
    TaskHistory,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskSuggestion,
    TaskType,
    task_dependencies,
)

# New models
from backend.src.models.agent import Agent
from backend.src.models.budget import CostRecord
from backend.src.models.conversation import ConversationMessage
from backend.src.models.goal import Goal
from backend.src.models.tenant import Tenant, TenantMember
from backend.src.models.executor_config import ExecutorConfig
from backend.src.models.worker import Worker

__all__ = [
    # Enums
    "DesignSessionStatus",
    "MessageRole",
    "MessageType",
    "PhaseStatus",
    "ProjectStatus",
    "SuggestionStatus",
    "TaskPriority",
    "TaskSource",
    "TaskStatus",
    "TaskType",
    "WorkspaceType",
    # Legacy models
    "DesignMessage",
    "DesignSession",
    "Phase",
    "Project",
    "Setting",
    "Task",
    "TaskHistory",
    "TaskSuggestion",
    "task_dependencies",
    # New models
    "Agent",
    "ConversationMessage",
    "CostRecord",
    "ExecutorConfig",
    "Goal",
    "Tenant",
    "TenantMember",
    "Worker",
]
