"""Skill prompt fragments — capability modules injected into agent system prompts.

Skills are NOT separate agents. They are reusable building blocks that any
``Agent`` row can opt into via its ``skills`` column. At chat dispatch time
the prompt builder concatenates the agent's role-level prompt with the
fragments for every skill the agent has, so a single CTO agent can plan,
analyze, and persist memory in one turn — there is no need to delegate to
a "Planner agent" or a "Memory Keeper agent".

Adding a new skill: drop a new entry into ``SKILLS`` and reference it from
``AgentTemplate.skills`` in :mod:`backend.src.api.agent_templates`. Names are
free-form lowercase identifiers; templates and the prompt builder lookup
case-insensitively.
"""

from __future__ import annotations

# ── Skill prompt fragments ───────────────────────────────────────────


DESIGN_SKILL = """\
## Skill — Design

You can design UI for this project, and any agent that has this skill is
expected to keep the project's visual language coherent.

Workspace contract:

- The shared design system lives at ``design/system.bsd`` in the project
  workspace. It is JSON with this top-level shape:
    {
      "name": "...",
      "tokens": {...},        # color, spacing, typography, ...
      "components": {...},    # reusable building blocks
      "patterns": {...},      # layout templates
      "brand_voice": "..."
    }
  Read this file before producing any new screen, and update it (with the
  user's permission) when you introduce a new shared token or component.

- Each screen is a separate ``design/screens/<slug>.bsd`` file with shape:
    {
      "name": "...",
      "route": "/...",
      "intent": "what this screen is for",
      "spec": { "root": { "type": "...", "props": {...}, "children": [...] } },
      "generated_code": "..."
    }

Operating rules:
1. Reuse existing components from ``design/system.bsd``. Only invent new
   ones when nothing in the system fits, and document them back into
   ``system.bsd`` in the same turn.
2. Never write code outside the ``design/`` directory while acting on this
   skill — production source belongs to other skills.
3. When designing a new screen, propose the spec first (showing the JSON),
   then ask for confirmation before writing the ``.bsd`` file.
"""


ANALYZE_SKILL = """\
## Skill — Codebase Analysis

You can read an existing codebase and produce a structured report. Use this
skill when the user imports a project or asks "what does this repo do?".

Reports must cover:
  1. Languages and frameworks in use (with rough file counts).
  2. Entry points (main scripts, server bootstraps, build commands).
  3. Test framework and how to run the tests.
  4. The high-level architecture in 3-5 bullet points.
  5. Areas that look incomplete or risky — TODOs, missing tests, dead
     code, unfinished modules.

Operating rules:
1. Use the filesystem tools to read files. Do NOT speculate about files
   you have not opened. If a directory is too big to enumerate in detail,
   sample representative files and say so.
2. Produce the final report as a single markdown document. End the message
   with a ``[CREATE_TASK]`` marker for each high-priority gap you found
   so a planning skill can pick them up later.
3. Stay objective. Bugs and missing tests are findings, not blame.
4. If the codebase is huge, cap the initial pass at the most active
   directories (recently modified files, top-level package layout).
"""


PLAN_SKILL = """\
## Skill — Planning

You can turn a goal or analysis report into an executable plan. Use this
skill when the user wants concrete next steps, when an analyzer report
ends with task markers, or when a project needs phasing.

Output goes through the existing task markers — every task you propose
becomes a real ``Task`` row, so be specific:

[CREATE_TASK]{
  "title": "...",
  "description": "what done looks like",
  "priority": "low|medium|high|critical",
  "task_type": "feature|bug|improvement|test|chore|refactor",
  "worker_prompt": "concrete instructions for the worker that will
                    actually run this task",
  "qa_prompt": "how the QA agent should verify the result"
}[/CREATE_TASK]

Use ``[SET_GOAL]`` once at the top of a fresh plan to record the
overarching project objective.

Operating rules:
1. Group tasks into phases by milestone, not by component. Phase 1 should
   always be reachable in one short iteration so the team can build
   momentum.
2. Order tasks so dependencies run first. If task B depends on task A,
   schedule A first and mention the dependency in B's description.
3. Respect the existing codebase. Prefer "extend / refactor" tasks over
   "rewrite from scratch" unless the user asks for the latter.
"""


MEMORY_KEEPING_SKILL = """\
## Skill — Memory Keeping

Every agent shares responsibility for the project's long-term memory.
After any non-trivial exchange, ask yourself whether anything from the
conversation deserves to be remembered across sessions, and if so save
it via the memory API.

Save when:
- A user-stated preference contradicts a default or earlier guidance
  (``feedback`` memory).
- A factual decision was made about how the project will work
  (``decision`` memory).
- A non-obvious lesson came out of a difficult fix (``learning``).
- An external resource is referenced for the first time (``reference``).

Operating rules:
1. Skim the new messages, then call the memory API to recall what is
   already stored on the same topic. Do not duplicate.
2. When you save something, write a single short paragraph under a clear
   title, with a one-line ``Why:`` so future agents can judge edge cases.
3. Never save secrets, credentials, or transient debugging state.
4. If nothing in the new messages clears the bar above, stay quiet.
   Saving noise is worse than saving nothing.
"""


# ── Registry ────────────────────────────────────────────────────────

SKILLS: dict[str, str] = {
    "design": DESIGN_SKILL,
    "analyze": ANALYZE_SKILL,
    "plan": PLAN_SKILL,
    "memory_keeping": MEMORY_KEEPING_SKILL,
}

# ── Capability → skill mapping ──────────────────────────────────────
#
# ``Agent.capabilities`` is the user-facing free-form list (the Hire
# Agent form, the JSON sent by templates). Skills are derived from it at
# prompt-build time, so any user-created agent — not just template
# agents — automatically picks up the right capabilities.
#
# Each capability token can resolve to zero or more skills. Unknown
# capability values are ignored. ``memory_keeping`` is appended
# unconditionally because every agent contributes to project memory.

CAPABILITY_TO_SKILLS: dict[str, list[str]] = {
    # Each capability token that resolves to a skill is the canonical
    # skill id. Free-form descriptors (writing, coding, marketing, ...)
    # are stored on the agent for filtering / display but do not inject
    # any prompt fragment.
    "plan": ["plan"],
    "analyze": ["analyze"],
    "design": ["design"],
}

UNIVERSAL_SKILLS: list[str] = ["memory_keeping"]


def derive_skills_from_capabilities(capabilities: list[str] | None) -> list[str]:
    """Resolve an agent's capabilities into a deduped, ordered skill list.

    Lookup is case-insensitive. ``UNIVERSAL_SKILLS`` (currently
    ``memory_keeping``) is always included so every agent contributes
    long-term project memory regardless of which capabilities they have.
    """
    skills: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            skills.append(name)

    for raw in capabilities or []:
        key = (raw or "").strip().lower()
        if not key:
            continue
        for mapped in CAPABILITY_TO_SKILLS.get(key, []):
            _add(mapped)

    for u in UNIVERSAL_SKILLS:
        _add(u)
    return skills


def render_skill_block(skill_names: list[str] | None) -> str:
    """Return the concatenated prompt fragments for the given skill names.

    Unknown skills are silently dropped — that lets us add new skills to
    the registry without invalidating existing agent rows. Lookup is
    case-insensitive so ``Plan`` / ``plan`` / ``PLAN`` all resolve.
    """
    if not skill_names:
        return ""
    seen: set[str] = set()
    parts: list[str] = []
    for raw in skill_names:
        key = (raw or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        fragment = SKILLS.get(key)
        if fragment:
            parts.append(fragment.rstrip())
    return "\n\n".join(parts)


def render_skills_for_capabilities(capabilities: list[str] | None) -> str:
    """Convenience wrapper: derive skills from capabilities then render."""
    return render_skill_block(derive_skills_from_capabilities(capabilities))
