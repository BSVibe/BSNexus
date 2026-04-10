"""Task marker parsing utilities used by agent_chat.

Action markers let an LLM agent emit structured task creation/modification
requests inside its natural-language response. These helpers extract and
strip those markers.
"""

from __future__ import annotations

import re

from backend.src import models

CREATE_TASK_RE = re.compile(r"\[CREATE_TASK\](.*?)\[/CREATE_TASK\]", re.DOTALL)
_MODIFY_TASK_RE = re.compile(r"\[MODIFY_TASK\](.*?)\[/MODIFY_TASK\]", re.DOTALL)


def strip_action_markers(text: str) -> str:
    """Remove action marker blocks from user-visible text."""
    text = CREATE_TASK_RE.sub("", text)
    text = _MODIFY_TASK_RE.sub("", text)
    return text.strip()


def build_project_context(project: models.Project) -> str:
    """Build a text summary of current project state for LLM prompts."""
    lines = [
        f"Project: {project.name}",
        f"Status: {project.status.value}",
        f"Description: {project.description}",
        "",
        "Phases:",
    ]
    for phase in sorted(project.phases, key=lambda p: p.order):
        lines.append(f"  [{phase.status.value}] {phase.name}")
        for task in sorted(phase.tasks, key=lambda t: t.created_at):
            task_type_label = f" ({task.task_type.value})" if task.task_type != models.TaskType.feature else ""
            lines.append(f"    - [{task.status.value}] {task.title}{task_type_label} (id: {task.id})")
    return "\n".join(lines)
