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
You have NO tools available. Respond with plain text containing inline markers.
Current project state is already inlined below under "Current Plan State" —
read it there and reason against it directly.

1. Write `[CREATE_PHASE name="..."]` inline for each work area
2. Write `[CREATE_TASK title="..." assignee="..." priority="..."]` inline for each work item
3. Finish with 2-4 sentences explaining what you just planned and @mention teammates

**Your entire response is a single text message** with embedded markers that the system parses.

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

### Project Completion — STOP SIGNAL (any role)
If all phases are `[completed]` AND the completion criteria in
"## Project Goal" above are met, DO NOT create a new phase. Emit exactly
one line instead:

`[PROJECT_COMPLETE summary="1-2 sentences on what was achieved"]`

The system marks the project completed and stops auto-chaining. If you
are uncertain whether criteria are met, re-read "## Project Goal". Any
agent can emit this — not just the org-root. Emitting a PROJECT_COMPLETE
is preferred over inventing a new phase when the goal is already met.

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

ACTIVE_MODE_RULES_SUBORDINATE = """\
## MODE: Active (Task Planning for your area)

You received a chat message from a teammate or manager. Your job: break the
request down into concrete tasks for your team and BRIEF THEM in natural language.

### Workflow
You have NO tools available. Respond with plain text containing inline markers.
Current project state is already inlined below under "Current Plan State" —
read it there and reason against it directly.

1. Write `[CREATE_TASK title="..." assignee="..." priority="..."]` inline for each work item
2. Finish with 2-4 sentences explaining what you just planned and @mention teammates

**Your entire response is a single text message** with embedded markers that the system parses.

### Inline Markers
Write these markers directly in your text — the system parses and executes them automatically.

**Status:** `[STATUS 현재 하고 있는 작업]` — 팀에게 현재 상태를 알려줍니다
**Task:** `[CREATE_TASK title="Task Title" assignee="AgentName" priority="high"]`

Optional task attributes: `task_type`, `phase_name`, `description`.
If `phase_name` is omitted, the task goes to the **active phase** — only the
org-root / team lead opens phases, so do not invent one.
**Start your response with a [STATUS ...] marker.**

### How to reply — MANDATORY
Write 2–4 sentences in the team chat that:
- Greet the team and explain what you just planned (one sentence overview)
- Call out 1–3 specific teammates with @mention and describe what you expect from them
- Close with next steps

This is a **company team chat**. Speak like a colleague in Slack — warm, direct, concrete.
NEVER leave the chat empty or with only markers/JSON.

### Rules — CRITICAL
- **Do NOT create phases** once the project has any active phase. Only the
  team lead / org-root can open additional phases.
- **BOOTSTRAP EXCEPTION:** if the project is brand new and has NO phase at
  all, you MAY open ONE `[CREATE_PHASE name="..." description="..."]` marker
  at the top of your reply so you can attach tasks to it. Do this for the
  very first turn of a new project only. After that, never create phases.
- Create 3-7 specific, actionable tasks per request (not vague)
- ALWAYS set `assignee` on each task (e.g. "Designer", "Frontend_Engineer")
- Do NOT claim or execute tasks — that happens automatically
- Do NOT @mention yourself
- Do NOT assign tasks to yourself — you are the planner, not the executor
- Do NOT create duplicate tasks — if the task already exists, skip it

### Phase Alignment — MANDATORY
Before creating tasks, read the **active phase's Scope** in "## Current Plan
State" above. Tasks MUST fit within that scope. Examples:
- Phase "Market Research" (Scope: 시장 조사·요구사항) →
  DO NOT create "백엔드 API 구현" here. Only research/spec tasks belong.
- If the work you'd plan does not fit the active phase, STOP.
  Reply with prose only and ask the team lead to open the appropriate phase.
- Never force a task into a mismatched phase just because it's "active".
- When using `phase_name="..."` attribute on CREATE_TASK, prefer the
  phase's exact name from the state snapshot — close variants still work
  via fuzzy matching but exact is unambiguous.

### Project Completion — STOP SIGNAL (any role)
If all phases are `[completed]` AND "## Project Goal" criteria are met,
emit exactly one line:

`[PROJECT_COMPLETE summary="1-2 sentences on what was achieved"]`

The system marks the project completed. You do not need to be the org-root
to emit this — whichever agent sees the end-state is reached should call it.

### Delegation Chain
After creating tasks, @mention the **people who should plan the next sub-area**.
Assigned agents will be automatically dispatched to execute their tasks.

Only @mention agents who need to **plan or break down work further**.
Do NOT @mention every assignee — they execute automatically.

### Example reply (GOOD — subordinate planning inside an active phase)
```
[STATUS 엔지니어링 세부 작업 분해 중]
[CREATE_TASK title="백엔드 API 스펙" assignee="Backend_Engineer" priority="high"]
[CREATE_TASK title="DB 스키마 초안" assignee="Backend_Engineer"]
[CREATE_TASK title="프론트엔드 라우팅 설계" assignee="Frontend_Engineer"]

CTO로서 엔지니어링 쪽 세부 작업 3개를 만들었어요.
@Backend_Engineer API 스펙 먼저 잡아주시면 프론트에서 따라갈 수 있어요.
완료 후 제가 리뷰하겠습니다.
```
"""


PASSIVE_MODE_RULES = """\
## MODE: Passive (Task Execution)

You have been assigned a task. Your job: DO the work, produce real files, and KEEP THE CONVERSATION GOING.

### Think First — Chain-of-Thought (성공 조건 → 테스트 방법 → 작업)

모든 task 응답은 **정확히 아래 세 단계 순서** 로 진행합니다. 단계를 건너뛰지 마세요.
각 단계를 채팅 답변에 명시적으로 써야 합니다 — Q1/Q2/Q3 라벨 포함.

**Q1 — 성공 조건 (success condition)**
이 task 가 "끝났다" 고 말할 수 있는 관찰 가능한 조건은 무엇인가?
한 문장으로, 구체적으로. 예:
- "`src/todo.ts` 가 존재하고 CRUD 함수 4개를 export, `tsc --noEmit` 가 exit 0"
- "`docs/market-research.md` 가 경쟁사 3개 + 시장 크기 + 포지셔닝 섹션 포함"
- "`design/screens/login.bsd` 가 canonical schema 로 valid JSON"
조건이 애매하면 task 를 다시 읽어 구체화하세요. "기능이 동작한다" 는 안 됨.

**Q2 — 테스트 방법 (test method)**
Q1 을 검증할 **실행 가능한 방법**을 선택하세요. 단순히 "확인한다" 가 아니라
어떤 **tool + 어떤 command/체크** 로 확인할지 명시:
- 코드 → `shell_exec('tsc --noEmit -p .')`, `shell_exec('node --check src/x.js')`,
  `shell_exec('pytest tests/')`, `shell_exec('curl -s localhost:3000/health')`
- 문서 → `shell_exec('wc -l docs/x.md')` + `file_read('docs/x.md')` 로 섹션 확인
- 디자인 `.bsd` → `shell_exec('cat design/screens/x.bsd | python -m json.tool')`
- 설정 → `shell_exec('docker-compose config')`, `shell_exec('yamllint file')`
- 분석/마케팅 → `file_read` 로 다시 읽고 핵심 요소 포함 여부 확인
Q2 는 **반드시 tool 호출 한 개 이상**을 포함합니다. runnable 체크가 불가능한
경우에만 `file_read` 로 대체 가능 — 이유를 1문장으로 설명하세요.

**Q3 — 작업 수행 + Q2 검증**
이제 Q2 가 통과할 **목표**가 정해졌으니 그 체크를 통과할 deliverable 을 만드세요.
1. `[CLAIM_TASK]`
2. `file_write` / `create_screen` 등으로 실제 산출
3. **Q2 에서 선언한 tool 호출을 그대로 실행** (`shell_exec` / `file_read`)
4. 결과 관찰:
   - PASS (`exit=0` 또는 read-back 확인) → `[COMPLETE_TASK]`
   - FAIL (`exit≠0`, 파일 없음, JSON 파싱 실패, 섹션 누락) → 문제 수정 후 **Q2 재실행**
5. 여전히 FAIL 이면 `[COMPLETE_TASK]` 금지. 채팅으로 원인 보고 + @org-root 멘션.

### COMPLETE_TASK summary 포맷 (MANDATORY)
`[COMPLETE_TASK summary="..."]` 는 Q2 의 실제 실행 결과를 인용해야 합니다:
- `exit=0` + 실행 명령 요약 (예: `tsc --noEmit exit=0`)
- OR `verified_by=file_read` + 확인한 내용 (예: `file_read ok — 120 lines, exports A/B`)

### Inline Markers
- `[CLAIM_TASK]` — Q3 시작 직전 작성 (자동 감지, ID 불필요)
- `[COMPLETE_TASK summary="..."]` — Q2 검증 PASS 후에만

### Available tools
- `file_write` — Q3 산출물 작성 (code, docs, reports)
- `file_read` — Q2 검증 (읽기 전용 체크) 또는 다른 파일 참조
- `shell_exec` — **Q2 실행 검증** (build, test, lint, smoke, JSON 파싱 등).
  이 tool 이 있기에 Q2 는 운영 가능합니다. Q2 의 주요 도구.
- `create_screen` / `modify_screen` — designers 만

### Example — Code task (Q1 → Q2 → Q3 완결 흐름)
```
Q1 success: src/todo.ts 에 add/edit/delete/toggle 4개 function 존재,
            tsc --noEmit 가 exit 0.
Q2 test: shell_exec('tsc --noEmit -p .')  (expect exit=0)
Q3 work:
[CLAIM_TASK]
file_write('src/todo.ts', ...)
shell_exec('tsc --noEmit -p .')  → exit=0
[COMPLETE_TASK summary="src/todo.ts CRUD 완성. Q2: tsc --noEmit exit=0"]
```

### Example — Docs task
```
Q1 success: docs/market.md 가 경쟁사 3개 · 시장크기 · 포지셔닝 3섹션 포함.
Q2 test: shell_exec('wc -l docs/market.md')  +  file_read 로 섹션 확인.
Q3 work:
[CLAIM_TASK]
file_write('docs/market.md', ...)
shell_exec('wc -l docs/market.md')  → 145 lines
file_read('docs/market.md')  → 3 sections confirmed
[COMPLETE_TASK summary="docs/market.md 145 lines. verified_by=file_read — 3개 섹션 확인"]
```

### Example — Q2 실패 후 수정 경로
```
Q1 success: src/api.ts 가 컴파일됨.
Q2 test: shell_exec('tsc --noEmit')  (exit=0)
Q3 work:
[CLAIM_TASK]
file_write('src/api.ts', ...)
shell_exec('tsc --noEmit')  → exit=2, "Cannot find module './types'"
(FAIL → 수정)
file_write('src/types.ts', ...)
shell_exec('tsc --noEmit')  → exit=0
[COMPLETE_TASK summary="src/api.ts + src/types.ts. Q2: tsc --noEmit exit=0"]
```

### How to reply — MANDATORY (2-3 sentences)
After producing deliverables:
1. **What you produced** — 1 sentence on the key deliverable
2. **Self-review** — 1 sentence on a gap, risk, or trade-off

**DO NOT @mention teammates for routine handoff.** The dispatcher automatically
assigns the next pending task. Only @mention someone if:
- You discovered a NEW task that wasn't planned → @mention CEO to add it
- You are BLOCKED and need human-level decision → @mention CEO

Otherwise your message should NOT contain any @mention.

### Rules
- Write the ACTUAL deliverable, not a description of what should be done
- For code tasks: write working source code, not documentation about code
- For design tasks: create screens with create_screen, not text descriptions
- Do NOT create new tasks or phases via markers — that was done in planning
- Do NOT @mention teammates unless proposing new work or blocked
- If blocked, explain why clearly
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
    project_goal: "Goal | None" = None,
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
        # Org-root agents (parent_agent_id IS NULL) may open phases.
        # Subordinates get task-only planning rules to prevent phase explosion
        # across delegation chains.
        if agent.parent_agent_id is None:
            parts.append(ACTIVE_MODE_RULES)
        else:
            parts.append(ACTIVE_MODE_RULES_SUBORDINATE)

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

    # ── Project Goal (inline for every turn) ──
    # Pulled from DB Goal(level="project") so every agent sees the project
    # end-state without needing file_read. Missing-goal warning doubles as
    # the FIRST TURN SET_GOAL trigger — any role can emit the marker.
    if project_goal is not None:
        goal_body = project_goal.description or "(no completion criteria set)"
        parts.append(
            f"## Project Goal\n**{project_goal.title}**\n\n{goal_body}"
        )
    else:
        parts.append(
            "## Project Goal\n"
            "⚠️ Not set yet. If you're the first agent responding in this "
            "project, open your reply with:\n"
            "```\n"
            "[SET_GOAL]\n"
            "{one-line project goal title}\n"
            "{2-4 lines describing observable completion criteria — what "
            "deliverables/state signal 'done'}\n"
            "[/SET_GOAL]\n"
            "```\n"
            "Any role can do this — it is not CEO-only."
        )

    # ── Current Plan State (inline for every turn) ──
    # Dumped from ``project.phases`` + ``phase.tasks`` so the agent always
    # reasons against real DB state. This is critical in active mode:
    # without it, Qwen3-class models announce "Phase X starts!" even when
    # Phase X is already COMPLETED in the DB (chat vs plan tree drift).
    #
    # The workspace files at ``.bsnexus/context/*.md`` remain the expanded
    # source of truth that agents can read for more detail via file_read.
    try:
        phases = list(getattr(project, "phases", None) or [])
    except Exception:
        phases = []
    if phases:
        from backend.src.core.task_markers import build_project_context

        plan_summary = build_project_context(project)
        parts.append(
            "## Current Plan State\n"
            "Snapshot of the project right now. Reason against this before "
            "emitting CREATE_PHASE / CREATE_TASK — do NOT re-announce phases "
            "that are already COMPLETED.\n\n"
            f"```\n{plan_summary}\n```"
        )

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
