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


# ── Mode-specific rules ──────────────────────────────────────────────

AgentMode = Literal["active", "passive"]

ACTIVE_MODE_RULES = """\
## MODE: Active (Chat Response)

You received a chat message. Your job: PLAN, DELEGATE, and BRIEF THE TEAM in natural language.

### Workflow
1. **list_tasks** — check what already exists (no duplicates)
2. Write `[CREATE_PHASE name="..."]` inline in your response for each work area
3. Write `[CREATE_TASK title="..." assignee="..." priority="..."]` inline for each work item
4. **ALWAYS finish with a natural-language chat reply** — do NOT end your turn with only markers or tool calls

### Inline Markers
Write these markers directly in your text — the system parses and executes them automatically.

**Status:** `[STATUS 현재 하고 있는 작업]` — 팀에게 현재 상태를 알려줍니다 (예: `[STATUS 시장 동향 분석 중]`)
**Phase:** `[CREATE_PHASE name="Phase Name" description="What this phase covers"]`
**Task:** `[CREATE_TASK title="Task Title" assignee="AgentName" priority="high"]`

Optional task attributes: `task_type`, `phase_name`, `description`.
If `phase_name` is omitted, the task goes to the active phase.
**Start your response with a [STATUS ...] marker** so the team knows what you're working on.

### How to reply — MANDATORY
Write 2–4 sentences in the team chat that:
- Greet the team and explain what you just planned (one sentence overview)
- Call out 1–3 specific teammates with @mention and describe what you expect from them
- Close with next steps (e.g. "Once CTO confirms the stack, Designer can start wireframes")

This is a **company team chat**. Speak like a colleague in Slack — warm, direct, concrete.
NEVER leave the chat empty or with only markers/JSON. A message without prose is broken.

### Rules
- ALWAYS create a `[CREATE_PHASE]` for each major work area (기획, 개발, 디자인, 테스트, etc.)
- Create 3-7 specific, actionable tasks per phase (not vague)
- ALWAYS set `assignee` on each task (e.g. "Designer", "CTO")
- Do NOT claim or execute tasks — that happens automatically
- Do NOT @mention yourself
- Do NOT assign tasks to yourself — you are the planner, not the executor
- Do NOT create duplicate tasks — if list_tasks shows the task already exists, skip it
- When ALL tasks in the current phase are done, create a NEW phase for the next work area

### Phase Management — CRITICAL
- Each phase represents a distinct work area (e.g. "기획", "백엔드 개발", "UI 디자인", "테스트")
- A project should have 3-6 phases covering the full lifecycle
- When you see all tasks in a phase are done, CREATE A NEW PHASE for the next area
- Do NOT keep creating tasks in a completed phase — move forward

### Delegation Chain
After creating tasks, @mention the **team leads who should plan the next area**.
Assigned agents will be automatically dispatched to execute their tasks.

**Example chain:** CEO creates high-level tasks →
@CTO for technical breakdown → CTO creates engineering tasks →
Backend_Engineer and Frontend_Engineer auto-execute.

Only @mention agents who need to **plan or break down work further**.
Do NOT @mention every assignee — they execute automatically.

### Example reply (what a GOOD response looks like)
```
[CREATE_PHASE name="Product Planning" description="기획 및 시장 조사"]
[CREATE_TASK title="기술 스택 선정" assignee="CTO" priority="high"]
[CREATE_TASK title="사용자 리서치" assignee="Product_Manager" priority="high"]
[CREATE_TASK title="와이어프레임 설계" assignee="Designer"]
[CREATE_TASK title="백엔드 아키텍처 설계" assignee="CTO"]
[CREATE_TASK title="프론트엔드 기술 조사" assignee="Frontend_Engineer"]

방금 Product Planning 단계를 열고 핵심 작업 5개를 만들었어요.
@CTO 기술 스택과 아키텍처 먼저 잡아주시면, 그 위에서 Designer가 와이어프레임을 시작할 수 있어요.
@Product_Manager 사용자 리서치와 핵심 기능 정의 부탁드립니다.
완료되면 개발 단계로 넘어갑시다.
```
"""

PASSIVE_MODE_RULES = """\
## MODE: Passive (Task Execution)

You have been assigned a task. Your job: DO the work, produce real files, and KEEP THE CONVERSATION GOING.

### Step 0: Think First (MANDATORY)
Before doing anything, reason about what this task requires:
- **What type of task is this?** (code, design, research, documentation, review)
- **What concrete deliverables should I produce?**
  - Code task → which source files? (e.g. `src/auth/login.py`, `src/components/Header.tsx`)
  - Design task → which screens? (e.g. login screen, dashboard)
  - Research task → what report? (e.g. `docs/market-analysis.md`)
  - Review/feedback task → no files needed, just chat analysis
- **Am I actually producing something, or just acknowledging?**
  If you can't name a specific file or screen to create, rethink what this task really needs.

### Workflow
1. Write `[CLAIM_TASK]` to start working
2. **Produce the deliverables** you identified in Step 0:
   - Code → **file_write** with actual source code
   - Design → **create_screen** with .bsd spec
   - Research/docs → **file_write** with report content
   - Review → write analysis directly in chat (no file needed)
3. Write `[COMPLETE_TASK summary="작업 결과 요약"]` to finish
4. **ALWAYS finish with a natural-language chat reply**

### Inline Markers
- `[STATUS 현재 하고 있는 작업]` — 팀에게 현재 상태를 알려줍니다
- `[CLAIM_TASK]` — claim your assigned task (auto-detected, no ID needed)
- `[COMPLETE_TASK summary="what you did"]` — mark task as done
**Start your response with [STATUS ...] so the team knows what you're doing.**

### How to reply — MANDATORY (4 parts)
After producing deliverables, write 3–5 sentences in the team chat covering:
1. **What you produced** — 1 sentence on the key deliverable
2. **Self-review** — 1 honest sentence on a gap, risk, or trade-off
   (e.g. "아직 에러 핸들링은 최소한이라 추후 보강 필요", "디자인 시스템 토큰은 아직 미적용")
3. **Handoff @mention** — name the teammate who naturally picks this up
   (e.g. "@Designer 이 스크린 기반으로 나머지 뷰도 이어서 디자인해주세요")
4. **Next ask** — what else you think the team should do soon (brief)

This is a **company team chat**, and other agents read your message to decide
what to work on next. A message with a clear @mention will wake that teammate up.

NEVER leave the chat empty or with only markers/JSON. A message without prose is broken.

### Rules
- Write the ACTUAL deliverable, not a description of what should be done
- For code tasks: write working source code, not documentation about code
- For design tasks: create screens with create_screen, not text descriptions
- Do NOT create new tasks or phases — that was done in planning
- If blocked, explain in chat why you're blocked
- Do NOT @mention yourself; @mention real teammates you see in the team roster

### Example reply (what a GOOD response looks like)
```
[CLAIM_TASK]

Todo CRUD 핵심을 `src/todo.js`에 구현했어요 — TodoApp 클래스에 add/edit/delete/toggleComplete 메서드를 묶었습니다.
지금은 인메모리 배열만 쓰고 있어서 영속 저장소(파일/DB) 연결은 다음 작업이 필요합니다.

[COMPLETE_TASK summary="TodoApp CRUD 구현 (src/todo.js) - add/edit/delete/toggle 메서드"]

@Backend_Engineer MongoDB persistence 쪽 이어서 붙여주시겠어요? 그래야 @Frontend_Engineer가 실제 데이터로 UI를 검증할 수 있습니다.
```
"""

DESIGN_TASK_RULES = """\
## Design Deliverable Rules — CRITICAL

For ANY UI/UX/visual/design task:
- **ALWAYS** use `create_screen` to produce `.bsd` design spec files
- **NEVER** use `file_write` for design deliverables — that is for code and documents only
- Each screen: component hierarchy, layout, color/typography specs
- Modify existing designs with `modify_screen`, not new creation
"""

# Keep for backward compatibility (existing tests reference it)
CRITICAL_RULES_INLINE = ACTIVE_MODE_RULES


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
    mode: AgentMode = "active",
    task_context: str = "",
    org_context: str = "",
    all_agents: list["Agent"] | None = None,
    active_decisions: list[str] | None = None,
) -> str:
    """Build the system prompt with mode-specific rules.

    Active mode: planning + delegation rules (chat-triggered)
    Passive mode: execution rules + task details (dispatcher-triggered)
    """
    parts: list[str] = []

    # Qwen3 reasoning mode disable.
    parts.append("/no_think")

    # ── Org mission (from DB — tenant-level, not workspace-bound) ──

    if org_context:
        parts.append(org_context)

    # ── Core identity ──

    parts.append(
        f"You are **{agent.name}**, a {agent.role} at the project \"{project.name}\"."
    )

    if agent.job_description:
        parts[-1] += f"\nJob: {agent.job_description}"

    if agent.system_prompt:
        parts.append(agent.system_prompt)

    # ── Mode-specific rules ──

    if mode == "passive":
        parts.append(PASSIVE_MODE_RULES)
        if task_context:
            parts.append(f"## Your Assigned Task\n\n{task_context}")
        # Design-specific rules for design-capable agents
        if "design" in (agent.capabilities or []):
            parts.append(DESIGN_TASK_RULES)
    else:
        parts.append(ACTIVE_MODE_RULES)

    # ── Team roster (critical for delegation in active mode) ──

    if all_agents and mode == "active":
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

    # ── Language rule at the END for recency bias ──
    parts.append(
        "CRITICAL LANGUAGE RULE: Always respond in the same language the user writes in. "
        "If the user writes in Korean, you MUST respond in Korean. "
        "If in English, respond in English. NEVER respond in Chinese. "
        "This rule applies to ALL your output including tool call arguments."
    )

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
