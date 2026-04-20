"""Task marker parsing utilities used by agent_chat.

Action markers let an LLM agent emit structured task creation/modification
requests inside its natural-language response. These helpers extract and
strip those markers.

Two marker formats are supported:

1. **Block markers** (legacy): ``[CREATE_TASK]...[/CREATE_TASK]``
2. **Inline markers** (preferred): ``[CREATE_TASK title="..." assignee="..."]``

Inline markers are one-liners with ``key="value"`` attributes. They are
cheaper for the LLM (no tool_use round-trip) and parsed by the backend
before the response text is stored.
"""

from __future__ import annotations

import re

from backend.src import models

# ── Legacy block markers ──────────────────────────────────────────
CREATE_TASK_RE = re.compile(r"\[CREATE_TASK\](.*?)\[/CREATE_TASK\]", re.DOTALL)
CREATE_PHASE_RE = re.compile(r"\[CREATE_PHASE\](.*?)\[/CREATE_PHASE\]", re.DOTALL)
_MODIFY_TASK_RE = re.compile(r"\[MODIFY_TASK\](.*?)\[/MODIFY_TASK\]", re.DOTALL)

# ── Inline markers (new) ─────────────────────────────────────────
# Matches: [CREATE_TASK title="..." assignee="..." priority="..."]
# Attributes are key="value" pairs in any order. Values may contain
# escaped quotes (\").
_INLINE_TASK_RE = re.compile(
    r"\[CREATE_TASK\s+((?:\w+=\"(?:[^\"\\]|\\.)*\"\s*)+)\]"
)
_INLINE_PHASE_RE = re.compile(
    r"\[CREATE_PHASE\s+((?:\w+=\"(?:[^\"\\]|\\.)*\"\s*)+)\]"
)
# [COMPLETE_TASK summary="..."] or just [COMPLETE_TASK]
_INLINE_COMPLETE_RE = re.compile(
    r"\[COMPLETE_TASK(?:\s+((?:\w+=\"(?:[^\"\\]|\\.)*\"\s*)+))?\]"
)
# [CLAIM_TASK] — no attributes needed (system finds the agent's pending task)
_INLINE_CLAIM_RE = re.compile(r"\[CLAIM_TASK\]")
# [PROJECT_COMPLETE summary="..."] or just [PROJECT_COMPLETE] — loop-breaker
_INLINE_PROJECT_COMPLETE_RE = re.compile(
    r"\[PROJECT_COMPLETE(?:\s+((?:\w+=\"(?:[^\"\\]|\\.)*\"\s*)+))?\]"
)
# Extracts individual key="value" pairs from an attribute string.
_ATTR_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _parse_attrs(attr_string: str) -> dict[str, str]:
    """Parse ``key="value"`` pairs, un-escaping backslash-quoted chars."""
    attrs: dict[str, str] = {}
    for key, raw_value in _ATTR_RE.findall(attr_string):
        attrs[key] = raw_value.replace('\\"', '"').replace("\\\\", "\\")
    return attrs


def parse_inline_task_markers(text: str) -> list[dict[str, str | None]]:
    """Extract inline ``[CREATE_TASK ...]`` markers from *text*.

    Returns a list of dicts with keys: ``title``, ``assignee``,
    ``priority``, ``task_type``, ``phase_name``, ``description``.
    Missing optional attributes are ``None``.

    ``assignee`` values are normalized — any leading ``@`` characters are
    stripped. Some local LLMs (notably GLM-4.7) emit
    ``assignee="@CEO"`` which would otherwise fail name resolution and
    leave the task with ``assigned_agent_id IS NULL``.
    """
    results: list[dict[str, str | None]] = []
    for m in _INLINE_TASK_RE.finditer(text):
        attrs = _parse_attrs(m.group(1))
        if "title" not in attrs:
            continue
        assignee = attrs.get("assignee")
        if assignee:
            assignee = assignee.lstrip("@ \t").strip() or None
        results.append({
            "title": attrs["title"],
            "assignee": assignee,
            "priority": attrs.get("priority"),
            "task_type": attrs.get("task_type"),
            "phase_name": attrs.get("phase_name"),
            "description": attrs.get("description"),
        })
    return results


def parse_inline_phase_markers(text: str) -> list[dict[str, str | None]]:
    """Extract inline ``[CREATE_PHASE ...]`` markers from *text*.

    Returns a list of dicts with keys: ``name``, ``description``.
    Missing optional attributes are ``None``.
    """
    results: list[dict[str, str | None]] = []
    for m in _INLINE_PHASE_RE.finditer(text):
        attrs = _parse_attrs(m.group(1))
        if "name" not in attrs:
            continue
        results.append({
            "name": attrs["name"],
            "description": attrs.get("description"),
        })
    return results


def parse_inline_complete_markers(text: str) -> list[dict[str, str | None]]:
    """Extract ``[COMPLETE_TASK ...]`` markers.

    Returns list of dicts with optional ``summary`` key.
    """
    results: list[dict[str, str | None]] = []
    for m in _INLINE_COMPLETE_RE.finditer(text):
        attrs = _parse_attrs(m.group(1) or "")
        results.append({"summary": attrs.get("summary")})
    return results


def has_claim_marker(text: str) -> bool:
    """Check if text contains ``[CLAIM_TASK]``."""
    return bool(_INLINE_CLAIM_RE.search(text))


def parse_inline_project_complete_markers(text: str) -> list[dict[str, str | None]]:
    """Extract ``[PROJECT_COMPLETE ...]`` markers.

    Returns a list of dicts with optional ``summary`` key. A bare
    ``[PROJECT_COMPLETE]`` (no attrs) yields ``{"summary": None}``.
    """
    results: list[dict[str, str | None]] = []
    for m in _INLINE_PROJECT_COMPLETE_RE.finditer(text):
        attrs = _parse_attrs(m.group(1) or "")
        results.append({"summary": attrs.get("summary")})
    return results


def strip_inline_markers(text: str) -> str:
    """Remove all inline markers from text."""
    text = _INLINE_TASK_RE.sub("", text)
    text = _INLINE_PHASE_RE.sub("", text)
    text = _INLINE_COMPLETE_RE.sub("", text)
    text = _INLINE_CLAIM_RE.sub("", text)
    text = _INLINE_PROJECT_COMPLETE_RE.sub("", text)
    return text.strip()


def strip_action_markers(text: str) -> str:
    """Remove all action marker blocks (legacy + inline) from user-visible text."""
    # Legacy block markers
    text = CREATE_TASK_RE.sub("", text)
    text = CREATE_PHASE_RE.sub("", text)
    text = _MODIFY_TASK_RE.sub("", text)
    # Inline markers
    text = _INLINE_TASK_RE.sub("", text)
    text = _INLINE_PHASE_RE.sub("", text)
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
