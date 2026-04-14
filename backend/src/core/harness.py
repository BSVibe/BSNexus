"""Harness — workspace-based prompt module system.

Replaces the monolithic inline system prompt with a layered architecture
inspired by Claude Code's CLAUDE.md / skills / rules pattern. Prompt
fragments live in the project workspace under ``.bsnexus/`` and are read
at dispatch time so:

- Prompts stay short (only load what the agent needs)
- Projects can customise rules (drop a ``.bsnexus/rules/custom.md``)
- Users can inspect / edit what their agents see
- ProjectDecisions inject global context automatically

Directory layout::

    .bsnexus/
    ├── rules/
    │   ├── response-format.md    # STATUS, markers, @mention rules
    │   ├── conflict-check.md     # check active decisions before working
    │   └── <user-custom>.md      # project-specific rules
    ├── skills/
    │   ├── design.md
    │   ├── analyze.md
    │   ├── plan.md
    │   └── memory-keeping.md
    └── context/                   # auto-generated at dispatch time
        ├── project.md             # project name, phases, tasks
        ├── team.md                # colleague roster
        ├── decisions.md           # active ProjectDecisions
        └── goals.md               # org mission + project goals

The assembler reads these files and concatenates them into the system
prompt. Files are plain markdown so they're readable in any editor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from backend.src.models import Agent, Goal, Project

logger = structlog.get_logger(__name__)

# ── Approval settings ───────────────────────────────────────────────

ApprovalLevel = Literal["auto_approve", "require_approval"]

DEFAULT_APPROVAL_SETTINGS: dict[str, ApprovalLevel] = {
    "phase_creation": "auto_approve",
    "task_creation": "auto_approve",
}


def read_approval_settings(workspace_dir: str | None) -> dict[str, ApprovalLevel]:
    """Read approval settings from .bsnexus/settings.json."""
    if not workspace_dir:
        return dict(DEFAULT_APPROVAL_SETTINGS)
    f = Path(workspace_dir) / HARNESS_DIR / "settings.json"
    if not f.is_file():
        return dict(DEFAULT_APPROVAL_SETTINGS)
    try:
        data = json.loads(f.read_text())
        approval = data.get("approval", {})
        result = dict(DEFAULT_APPROVAL_SETTINGS)
        for key in result:
            if key in approval and approval[key] in ("auto_approve", "require_approval"):
                result[key] = approval[key]
        return result
    except Exception:
        return dict(DEFAULT_APPROVAL_SETTINGS)


def write_approval_settings(workspace_dir: str | None, settings: dict[str, ApprovalLevel]) -> None:
    """Write approval settings to .bsnexus/settings.json."""
    if not workspace_dir:
        return
    f = Path(workspace_dir) / HARNESS_DIR / "settings.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if f.is_file():
        try:
            existing = json.loads(f.read_text())
        except Exception:
            pass
    existing["approval"] = settings
    f.write_text(json.dumps(existing, indent=2, ensure_ascii=False))

HARNESS_DIR = ".bsnexus"


# ── Critical rules — always inlined into system prompt ──────────────

CRITICAL_RULES_INLINE = """\
## CRITICAL: You MUST use tools, not just text

**NEVER just write text.** Every response MUST include tool calls.

### Workflow — ALWAYS follow these steps
1. **list_tasks** — check existing tasks first (no duplicates)
2. **create_phase** if no phase exists for this work area
3. **create_task** for EACH specific work item (create multiple tasks!)
4. **claim_task** to mark a task as yours
5. Do the work using tools: **file_write** to save results, **file_read** to research
6. **complete_task** with a summary when done

### Delegation — you are part of a team
- Only **claim_task** for tasks matching YOUR role/expertise
- For tasks outside your expertise, create the task AND @mention a teammate
- **IMPORTANT**: After creating tasks, write a text message with @mentions like:
  "@CTO 기술 스택을 결정해주세요. @Designer UI 디자인을 만들어주세요."
- Do NOT @mention yourself. Do NOT do everything yourself.
- A CEO/leader should plan and delegate, NOT execute every task.

### Output your work as files
- **file_write** to save research results, reports, analysis as .md files
- Save files to `docs/`, `reports/`, or `research/` (NOT inside `.bsnexus/`)
- **create_screen** to create UI designs as .bsd files (Designer only)
- Do NOT just describe your work in text — SAVE it to workspace files
"""


# ── Seed: write default harness files into a workspace ──────────────


RULES_RESPONSE_FORMAT = """\
# Response Format & Tools

## CRITICAL: Task-Centric Work

All work MUST flow through tasks. Do NOT just reply with text.

**Workflow for every request:**
1. **create_phase** first if no phase exists for this work area
2. **create_task** for the specific work item
3. **claim_task** to mark it as yours
4. Do the actual work (file_write, research, analysis, etc.)
5. **complete_task** with a summary when done

**Before starting work**, always check **list_tasks** to see if a
relevant task already exists. Claim and complete it instead of creating
duplicates.

### Task Types
- `chore`: research, analysis, discussions, decisions, reviews
- `feature`: implementation, new functionality
- `bug`, `improvement`, `test`, `refactor`: as named

### Phase Grouping

Use **create_phase** to organize related tasks. Phases are categories,
not sequential stages. **You must create a phase before creating tasks.**
**Create only ONE phase per work area. Check existing phases first with list_tasks.**

### Goals & Decisions

- **set_goal**: Set or update the project-level goal
- **record_decision**: Record a confirmed project direction

### File Operations

- **file_read** / **file_write** / **list_files**: workspace file ops
- **create_screen** / **modify_screen**: .bsd design specs

## Delegation — CRITICAL

You are part of a team. **Do NOT do everything yourself.**

**Rules:**
1. Create the phase and tasks for the overall plan
2. Only **claim_task** and work on tasks that match YOUR role/expertise
3. For tasks outside your expertise, **@mention the best team member**
   in your response text. They will automatically receive your message.
4. **Do NOT @mention yourself.** Only mention OTHER agents.
5. A CEO/leader should plan and delegate, NOT execute every task.

**Example:** If you are CEO and the plan needs market research + tech stack:
- Create tasks for both
- @CMO for market research task (do NOT claim it yourself)
- @CTO for tech stack task (do NOT claim it yourself)
- Only claim tasks that specifically need CEO decision-making
"""


RULES_CONFLICT_CHECK = """\
# Conflict Check

Review **Active Decisions** before starting work. Only skip your task
if a decision **explicitly reverses or cancels** it (e.g., "we decided
NOT to do X"). A decision that describes a plan or priority does NOT
conflict with tasks that are part of that plan.

If there is a genuine conflict:
1. Do NOT proceed.
2. Explain the conflict briefly and suggest an alternative.

**Important:** A decision like "prioritize market research" does NOT
conflict with doing market research — it confirms it. Only contradictions
are conflicts.
"""


RULES_COMMUNICATION = """\
# Communication

- Communicate professionally and respectfully — use polite language
  (존댓말 in Korean).
- Focus only on the project described below. Do not assume the product
  being built is the platform you are running on.
- When responding to a delegation from another agent, address them by
  name and reference what they asked.
"""


def seed_harness(workspace_dir: str | Path) -> None:
    """Write the default .bsnexus/ files into a workspace if they don't exist.

    Called when a project is created or when the first chat message is
    sent to a project that lacks a harness directory. Existing files are
    never overwritten — the user may have customised them.
    """
    root = Path(workspace_dir) / HARNESS_DIR
    rules = root / "rules"
    skills = root / "skills"
    context = root / "context"

    for d in (rules, skills, context):
        d.mkdir(parents=True, exist_ok=True)

    _write_if_absent(rules / "response-format.md", RULES_RESPONSE_FORMAT)
    _write_if_absent(rules / "conflict-check.md", RULES_CONFLICT_CHECK)
    _write_if_absent(rules / "communication.md", RULES_COMMUNICATION)

    # Skills are seeded from the canonical fragments in prompts/skills.py
    # so there's a single source of truth during development. Once the
    # project matures, users can edit the workspace copies directly.
    from backend.src.prompts.skills import SKILLS

    for skill_id, fragment in SKILLS.items():
        _write_if_absent(skills / f"{skill_id}.md", fragment)


def _write_if_absent(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content)


# ── Assemble: read harness files at dispatch time ───────────────────


async def assemble_system_prompt(
    agent: "Agent",
    project: "Project",
    workspace_dir: str | None,
    *,
    org_context: str = "",
    all_agents: list["Agent"] | None = None,
    active_decisions: list[str] | None = None,
) -> str:
    """Build the system prompt by inlining critical rules and reading .bsnexus/.

    Core workflow and delegation rules are always inlined so local models
    (Qwen3-14B etc.) follow them without needing a file_read call.
    Workspace rules and agent skills are read from .bsnexus/ and appended.
    Project-level context (goals, decisions) stays in .bsnexus/context/
    and agents read them via file_read — like Claude Code reads .claude/.
    Org-level context (mission) is inlined because it lives in DB, not files.
    """
    parts: list[str] = []

    # Qwen3 reasoning mode disable.
    parts.append("/no_think")

    # ── Org mission (from DB — tenant-level, not workspace-bound) ──

    if org_context:
        parts.append(org_context)

    # ── Core identity ──

    parts.append(
        f"LANGUAGE: Always respond in the same language the user writes in. "
        f"If the user writes in Korean, you MUST respond in Korean. "
        f"If in English, respond in English. NEVER respond in Chinese.\n\n"
        f"You are **{agent.name}**, a {agent.role} at the project \"{project.name}\"."
    )

    if agent.job_description:
        parts[-1] += f"\nJob: {agent.job_description}"

    if agent.system_prompt:
        parts.append(agent.system_prompt)

    # ── Critical rules (always inlined — local models skip file_read) ──

    parts.append(CRITICAL_RULES_INLINE)

    # ── Team roster (critical for delegation) ──

    if all_agents:
        colleagues = [a for a in all_agents if a.id != agent.id and a.is_active]
        if colleagues:
            lines = ["## Team — @mention to delegate"]
            for a in colleagues:
                desc = f"- @{a.name} ({a.role})"
                if a.job_description:
                    desc += f" — {a.job_description}"
                lines.append(desc)
            parts.append("\n".join(lines))

    # ── Workspace rules ──
    # NOT inlined — they're large and covered by CRITICAL_RULES_INLINE.
    # Agents can file_read .bsnexus/rules/ for details.

    # ── Agent skills (one-liner summaries, not full fragments) ──
    # Full skill fragments are in .bsnexus/skills/ for file_read.
    # Only inject short summaries to keep prompt small for local models.

    skill_summaries = _build_skill_summaries(agent)
    if skill_summaries:
        parts.append(skill_summaries)

    # ── .bsnexus/ context reference (like Claude Code's .claude/) ──

    parts.append(
        "## .bsnexus/ — Project Knowledge Base\n"
        "Before starting work, **read these files with file_read**:\n"
        "- `.bsnexus/context/project.md` — current phases, tasks, project state\n"
        "- `.bsnexus/context/goals.md` — project goals and priorities\n"
        "- `.bsnexus/context/decisions.md` — active decisions (do NOT contradict)\n"
        "These files are your source of truth. Read them before every task."
    )

    # ── Active decisions (inline only if short — prevents contradictions) ──
    if active_decisions and len(active_decisions) <= 3:
        lines = ["Active decisions:"]
        for d in active_decisions:
            lines.append(f"  - {d}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _read_harness_dir(workspace_dir: str | None, subdir: str) -> str:
    """Read and concatenate all .md files in a harness subdirectory."""
    if not workspace_dir:
        return ""
    d = Path(workspace_dir) / HARNESS_DIR / subdir
    if not d.is_dir():
        return ""
    parts: list[str] = []
    for f in sorted(d.glob("*.md")):
        try:
            content = f.read_text().strip()
            if content:
                parts.append(content)
        except Exception:
            logger.warning("harness_read_error", path=str(f), exc_info=True)
    return "\n\n".join(parts)


SKILL_SUMMARIES: dict[str, str] = {
    "plan": "Planning — turn goals into phases and tasks with priorities",
    "analyze": "Analysis — read codebases and produce structured reports",
    "design": "Design — create UI screens as .bsd files via create_screen tool",
    "architect": "Architecture — evaluate tech stacks and produce architecture diagrams",
    "marketing": "Marketing — landing page copy, acquisition channels, content calendars",
    "memory_keeping": "Memory — save important decisions and context across sessions",
}


def _build_skill_summaries(agent: "Agent") -> str:
    """Build compact skill one-liners for the agent's capabilities."""
    from backend.src.prompts.skills import CAPABILITY_TO_SKILLS, UNIVERSAL_SKILLS

    skill_ids: set[str] = set()
    for cap in (agent.capabilities or []):
        key = (cap or "").strip().lower()
        for sid in CAPABILITY_TO_SKILLS.get(key, []):
            skill_ids.add(sid)
    for u in UNIVERSAL_SKILLS:
        skill_ids.add(u)

    if not skill_ids:
        return ""

    lines = ["## Your Skills"]
    for sid in sorted(skill_ids):
        summary = SKILL_SUMMARIES.get(sid)
        if summary:
            lines.append(f"- {summary}")
    lines.append("Details in `.bsnexus/skills/` — use file_read if needed.")
    return "\n".join(lines)


def _read_harness_file(workspace_dir: str | None, path: str) -> str:
    """Read a single harness file."""
    if not workspace_dir:
        return ""
    f = Path(workspace_dir) / HARNESS_DIR / path
    if not f.is_file():
        return ""
    try:
        return f.read_text().strip()
    except Exception:
        return ""


def _read_agent_skills(workspace_dir: str | None, agent: "Agent") -> str:
    """Read skill .md files that match the agent's capabilities."""
    if not workspace_dir:
        return ""
    from backend.src.prompts.skills import CAPABILITY_TO_SKILLS, UNIVERSAL_SKILLS

    skill_ids: set[str] = set()
    for cap in (agent.capabilities or []):
        key = (cap or "").strip().lower()
        for sid in CAPABILITY_TO_SKILLS.get(key, []):
            skill_ids.add(sid)
    for u in UNIVERSAL_SKILLS:
        skill_ids.add(u)

    skills_dir = Path(workspace_dir) / HARNESS_DIR / "skills"
    if not skills_dir.is_dir():
        return ""

    parts: list[str] = []
    for sid in sorted(skill_ids):
        f = skills_dir / f"{sid}.md"
        if f.is_file():
            try:
                content = f.read_text().strip()
                if content:
                    parts.append(content)
            except Exception:
                pass
    return "\n\n".join(parts)


# ── Context: write dynamic context files before dispatch ────────────


async def refresh_context(
    workspace_dir: str | None,
    project: "Project",
    all_agents: list["Agent"],
    goals: list["Goal"],
) -> None:
    """Overwrite .bsnexus/context/ files with fresh data.

    Called just before prompt assembly so the workspace files reflect
    the latest DB state. These files are auto-generated — users should
    not edit them (they'll be overwritten on the next chat turn).

    Note: decisions.md is NOT overwritten here — it is managed
    exclusively by ``_execute_decision_markers`` in agent_chat.py
    (append-only on [DECISION] markers). This prevents refresh from
    clobbering decisions that agents created mid-conversation.
    """
    if not workspace_dir:
        return
    ctx = Path(workspace_dir) / HARNESS_DIR / "context"
    ctx.mkdir(parents=True, exist_ok=True)

    # project.md
    from backend.src.core.task_markers import build_project_context
    (ctx / "project.md").write_text(build_project_context(project))

    # team.md
    lines = ["# Team\n"]
    for a in all_agents:
        if a.is_active:
            desc = f"- @{a.name} ({a.role})"
            if a.job_description:
                desc += f" — {a.job_description}"
            lines.append(desc)
    (ctx / "team.md").write_text("\n".join(lines))

    # goals.md
    if goals:
        lines = ["# Goals\n"]
        for g in goals:
            lines.append(f"- [{g.level}] {g.title}")
            if g.description:
                lines.append(f"  {g.description}")
        (ctx / "goals.md").write_text("\n".join(lines))
