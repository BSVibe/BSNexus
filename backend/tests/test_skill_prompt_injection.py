"""Skill prompt injection — every capability on an agent shows up in its system prompt.

Capabilities are the user-facing knob (the Hire Agent form, the JSON sent
by templates). Skills are the prompt-fragment modules; they get derived
from capabilities at chat-build time so a single CTO with
``capabilities=["plan","analyze","coding"]`` automatically gets all three
matching skill blocks injected — no role hardcoding, no separate
"specialist" agents.
"""

from __future__ import annotations

import uuid

from backend.src.api.agent_chat import _build_system_prompt
from backend.src.models import Agent, Project, ProjectStatus
from backend.src.prompts.skills import (
    SKILLS,
    derive_skills_from_capabilities,
    render_skill_block,
    render_skills_for_capabilities,
)


def _agent(capabilities: list[str] | None) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="Test",
        role="cto",
        title="CTO",
        job_description="Tech lead",
        executor_type="claude_api",
        executor_config={},
        capabilities=capabilities or [],
        is_active=True,
    )


def _project() -> Project:
    return Project(
        id=uuid.uuid4(),
        name="Test Project",
        description="x",
        status=ProjectStatus.active,
    )


# ── derive_skills_from_capabilities ──────────────────────────────────


def test_derive_skills_returns_only_universal_for_empty_capabilities() -> None:
    assert derive_skills_from_capabilities(None) == ["memory_keeping"]
    assert derive_skills_from_capabilities([]) == ["memory_keeping"]


def test_derive_skills_resolves_direct_tokens() -> None:
    skills = derive_skills_from_capabilities(["plan", "analyze", "design"])
    assert "plan" in skills
    assert "analyze" in skills
    assert "design" in skills
    assert "memory_keeping" in skills


def test_derive_skills_dedupes_repeated_canonical_tokens() -> None:
    # Only canonical tokens resolve — duplicates collapse.
    skills = derive_skills_from_capabilities(["analyze", "Analyze", "ANALYZE"])
    assert skills.count("analyze") == 1


def test_derive_skills_ignores_unknown_freeform_descriptors() -> None:
    # Free-form descriptors that exist on the agent for filtering / display
    # (coding, writing, marketing, ...) do NOT inject any skill block.
    skills = derive_skills_from_capabilities(["coding", "writing", "marketing"])
    assert skills == ["memory_keeping"]


def test_derive_skills_drops_unknown_tokens() -> None:
    skills = derive_skills_from_capabilities(["totally_made_up", "writing", "plan"])
    # writing has no skill mapping, made-up is unknown, plan resolves
    assert "plan" in skills
    assert "memory_keeping" in skills
    assert "totally_made_up" not in skills


def test_derive_skills_is_case_insensitive() -> None:
    skills = derive_skills_from_capabilities(["Plan", "ANALYZE", "DeSiGn"])
    assert "plan" in skills
    assert "analyze" in skills
    assert "design" in skills


# ── render_skill_block / render_skills_for_capabilities ─────────────


def test_render_skill_block_drops_unknown_skills() -> None:
    block = render_skill_block(["plan", "totally_made_up", "memory_keeping"])
    assert "Skill — Planning" in block
    assert "Memory Keeping" in block
    assert "totally_made_up" not in block


def test_render_skills_for_capabilities_chains_resolution_and_render() -> None:
    block = render_skills_for_capabilities(["plan", "design"])
    assert "Skill — Planning" in block
    assert "Skill — Design" in block
    assert "Skill — Memory Keeping" in block  # universal
    assert "Skill — Codebase Analysis" not in block


# ── _build_system_prompt integration ────────────────────────────────


def test_build_system_prompt_injects_each_capability_skill_fragment() -> None:
    agent = _agent(["plan", "analyze", "coding"])
    prompt = _build_system_prompt(
        agent=agent,
        project=_project(),
        goal_context="",
        all_agents=[agent],
    )
    assert "Skill — Planning" in prompt
    assert "Skill — Codebase Analysis" in prompt
    assert "Skill — Memory Keeping" in prompt
    # design was not in capabilities, must not be injected.
    assert "Skill — Design" not in prompt


def test_build_system_prompt_for_writing_only_agent_has_only_memory_skill() -> None:
    """A writer with no skill-mapped capabilities still keeps memory_keeping."""
    agent = _agent(["writing", "marketing"])
    prompt = _build_system_prompt(
        agent=agent,
        project=_project(),
        goal_context="",
        all_agents=[agent],
    )
    assert "Skill — Memory Keeping" in prompt
    for not_present in ("Planning", "Design", "Codebase Analysis"):
        assert f"Skill — {not_present}" not in prompt


def test_build_system_prompt_for_agent_without_capabilities_still_has_memory_block() -> None:
    """memory_keeping is universal — every agent gets it even without any caps."""
    agent = _agent(None)
    prompt = _build_system_prompt(
        agent=agent,
        project=_project(),
        goal_context="",
        all_agents=[agent],
    )
    assert "Skill — Memory Keeping" in prompt
    # The other 3 fragment headers must not appear.
    for fragment_id in ("design", "analyze", "plan"):
        header = SKILLS[fragment_id].splitlines()[0]
        assert header not in prompt
