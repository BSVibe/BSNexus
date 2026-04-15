"""Tests for the harness — workspace-based prompt module system."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.src.core.harness import (
    HARNESS_DIR,
    assemble_system_prompt,
    refresh_context,
    seed_harness,
)
from backend.src.models import Agent, Project, ProjectStatus


def _agent(capabilities: list[str] | None = None) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="TestAgent",
        role="engineer",
        title="Engineer",
        job_description="Writes code",
        executor_type="generic_llm",
        executor_config={},
        capabilities=capabilities or ["coding"],
        is_active=True,
    )


def _project() -> Project:
    return Project(
        id=uuid.uuid4(),
        name="TestProject",
        description="A test project",
        status=ProjectStatus.active,
    )


class TestSeedHarness:
    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        harness = tmp_path / HARNESS_DIR
        assert (harness / "rules").is_dir()
        assert (harness / "skills").is_dir()
        assert (harness / "context").is_dir()

    def test_creates_default_rule_files(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        rules = tmp_path / HARNESS_DIR / "rules"
        assert (rules / "response-format.md").is_file()
        assert (rules / "conflict-check.md").is_file()
        assert (rules / "communication.md").is_file()
        # Content sanity — now tool-based instead of marker-based
        content = (rules / "response-format.md").read_text()
        assert "create_task" in content
        assert "claim_task" in content

    def test_creates_skill_files(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        skills = tmp_path / HARNESS_DIR / "skills"
        assert (skills / "design.md").is_file()
        assert (skills / "analyze.md").is_file()
        assert (skills / "plan.md").is_file()
        assert (skills / "memory_keeping.md").is_file()

    def test_does_not_overwrite_existing_files(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        custom = tmp_path / HARNESS_DIR / "rules" / "response-format.md"
        custom.write_text("USER CUSTOMISED")
        seed_harness(tmp_path)
        assert custom.read_text() == "USER CUSTOMISED"

    def test_user_custom_rules_are_preserved(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        custom = tmp_path / HARNESS_DIR / "rules" / "my-project-rule.md"
        custom.write_text("Always use Korean variable names")
        seed_harness(tmp_path)
        assert custom.read_text() == "Always use Korean variable names"


class TestAssembleSystemPrompt:
    @pytest.mark.asyncio
    async def test_includes_agent_identity(self, tmp_path: Path) -> None:
        agent = _agent()
        project = _project()
        prompt = await assemble_system_prompt(agent, project, str(tmp_path))
        assert "TestAgent" in prompt
        assert "engineer" in prompt
        assert "TestProject" in prompt
        assert "Writes code" in prompt

    @pytest.mark.asyncio
    async def test_includes_active_decisions(self, tmp_path: Path) -> None:
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
            active_decisions=["녹음 앱으로 방향 확정", "React + FastAPI 기술 스택 확정"],
        )
        assert "녹음 앱으로 방향 확정" in prompt
        assert "React + FastAPI" in prompt
        assert "Active decisions" in prompt

    @pytest.mark.asyncio
    async def test_reads_rules_from_workspace(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
        )
        assert "create_task" in prompt
        assert "PLAN and DELEGATE" in prompt or "Active" in prompt

    @pytest.mark.asyncio
    async def test_reads_skills_for_capabilities(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        agent = _agent(["plan", "analyze", "coding"])
        prompt = await assemble_system_prompt(
            agent, _project(), str(tmp_path),
        )
        assert "Planning" in prompt
        assert "Analysis" in prompt
        assert "Memory" in prompt
        assert "Design" not in prompt or "Your Skills" in prompt

    @pytest.mark.asyncio
    async def test_workspace_rules_not_inlined(self, tmp_path: Path) -> None:
        """Workspace rules are NOT inlined — too large for local models.
        Agents read them via file_read from .bsnexus/rules/."""
        seed_harness(tmp_path)
        custom = tmp_path / HARNESS_DIR / "rules" / "custom.md"
        custom.write_text("# Custom Rule\nAlways respond in Korean.")
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
        )
        # Custom rules stay in files, not in prompt
        assert "Always respond in Korean" not in prompt
        # But .bsnexus reference tells agents to read them
        assert ".bsnexus" in prompt

    @pytest.mark.asyncio
    async def test_includes_team_roster(self, tmp_path: Path) -> None:
        agent1 = _agent()
        agent2 = _agent()
        agent2.name = "Designer"
        agent2.role = "designer"
        agent2.id = uuid.uuid4()
        prompt = await assemble_system_prompt(
            agent1, _project(), str(tmp_path),
            all_agents=[agent1, agent2],
        )
        assert "@Designer" in prompt

    @pytest.mark.asyncio
    async def test_active_mode_has_planning_rules(self, tmp_path: Path) -> None:
        """Active mode includes planning + delegation rules."""
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
        )
        assert "create_task" in prompt
        assert "create_phase" in prompt

    @pytest.mark.asyncio
    async def test_passive_mode_has_execution_rules(self, tmp_path: Path) -> None:
        """Passive mode includes execution rules + task context."""
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
            mode="passive",
            task_context="Task ID: abc\nTitle: Design UI",
        )
        assert "claim_task" in prompt
        assert "complete_task" in prompt
        assert "Design UI" in prompt
        assert "create_task" not in prompt or "Do NOT create" in prompt

    @pytest.mark.asyncio
    async def test_fallback_when_no_workspace(self) -> None:
        """No workspace_dir → mode rules + skill summaries still present."""
        prompt = await assemble_system_prompt(
            _agent(), _project(), None,
        )
        assert "create_task" in prompt  # active mode default
        # Skill summaries present
        assert "Memory" in prompt

    # ── Issue #8: Design prompt injection ──

    @pytest.mark.asyncio
    async def test_passive_design_agent_gets_design_rules(self, tmp_path: Path) -> None:
        agent = _agent(["design"])
        prompt = await assemble_system_prompt(
            agent, _project(), str(tmp_path), mode="passive",
        )
        assert "MUST" in prompt and "create_screen" in prompt
        assert "NEVER" in prompt and "file_write" in prompt

    @pytest.mark.asyncio
    async def test_passive_coding_agent_no_design_rules(self, tmp_path: Path) -> None:
        agent = _agent(["coding"])
        prompt = await assemble_system_prompt(
            agent, _project(), str(tmp_path), mode="passive",
        )
        # Generic passive rules present, but no design-specific rules
        assert "claim_task" in prompt
        assert "ALWAYS" not in prompt or "create_screen" not in prompt

    @pytest.mark.asyncio
    async def test_active_design_agent_no_design_rules(self, tmp_path: Path) -> None:
        agent = _agent(["design"])
        prompt = await assemble_system_prompt(
            agent, _project(), str(tmp_path), mode="active",
        )
        # Active mode should not have the design deliverable rules
        assert "Design Deliverable Rules" not in prompt


class TestRefreshContext:
    @pytest.mark.asyncio
    async def test_writes_context_files(self, tmp_path: Path) -> None:
        project = _project()
        project.phases = []
        agent = _agent()
        await refresh_context(str(tmp_path), project, [agent], goals=[])
        ctx = tmp_path / HARNESS_DIR / "context"
        assert (ctx / "project.md").is_file()
        assert (ctx / "team.md").is_file()
        assert "@TestAgent" in (ctx / "team.md").read_text()

    @pytest.mark.asyncio
    async def test_does_not_overwrite_decisions_file(self, tmp_path: Path) -> None:
        """refresh_context must NOT touch decisions.md — that file is
        managed exclusively by _execute_decision_markers."""
        project = _project()
        project.phases = []
        ctx = tmp_path / HARNESS_DIR / "context"
        ctx.mkdir(parents=True, exist_ok=True)
        (ctx / "decisions.md").write_text("# Active Decisions\n\n1. 녹음 앱")
        await refresh_context(str(tmp_path), project, [], goals=[])
        # decisions.md must still contain the original content
        assert "녹음 앱" in (ctx / "decisions.md").read_text()


class TestDecisionMarkers:
    def test_strip_all_markers_removes_decision(self) -> None:
        from backend.src.api.agent_chat import _strip_all_markers
        text = "[STATUS] Working\n[DECISION] A confirmed [/DECISION]\nHello world"
        stripped = _strip_all_markers(text)
        assert "[DECISION]" not in stripped
        assert "[STATUS]" not in stripped
        assert "Hello world" in stripped

    def test_load_decisions_from_workspace(self, tmp_path: Path) -> None:
        from backend.src.api.agent_chat import _load_decisions_from_workspace
        from backend.src.core.harness import HARNESS_DIR

        # No file → empty
        assert _load_decisions_from_workspace(str(tmp_path)) == []

        # With file
        ctx = tmp_path / HARNESS_DIR / "context"
        ctx.mkdir(parents=True)
        (ctx / "decisions.md").write_text(
            "# Active Decisions\n\nThese are confirmed.\n\n1. 녹음 앱\n2. React 기술 스택"
        )
        result = _load_decisions_from_workspace(str(tmp_path))
        assert len(result) == 2
        assert "녹음 앱" in result[0]

    def test_load_decisions_no_workspace(self) -> None:
        from backend.src.api.agent_chat import _load_decisions_from_workspace
        assert _load_decisions_from_workspace(None) == []

    # Decision marker execution tests removed — now handled by RecordDecisionTool.
    # See backend/tests/test_tools/ for tool-based decision tests.


class TestCreatePhaseMarker:
    def test_strip_all_markers_removes_create_phase(self) -> None:
        from backend.src.api.agent_chat import _strip_all_markers
        text = "[STATUS] Working\n[CREATE_PHASE]{\"name\": \"Phase 1\"}[/CREATE_PHASE]\nHello"
        stripped = _strip_all_markers(text)
        assert "[CREATE_PHASE]" not in stripped
        assert "Hello" in stripped

    def test_create_phase_re_parses_json(self) -> None:
        from backend.src.core.task_markers import CREATE_PHASE_RE
        text = '[CREATE_PHASE]{"name": "Research", "description": "Market research"}[/CREATE_PHASE]'
        matches = CREATE_PHASE_RE.findall(text)
        assert len(matches) == 1
        import json
        data = json.loads(matches[0])
        assert data["name"] == "Research"

    # Phase marker execution tests removed — now handled by CreatePhaseTool.
    # See backend/tests/test_tools/ for tool-based phase creation tests.
