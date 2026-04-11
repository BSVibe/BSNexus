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
        executor_type="claude_api",
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
        # Content sanity
        assert "[STATUS]" in (rules / "response-format.md").read_text()
        assert "[DECISION]" in (rules / "response-format.md").read_text()
        assert "[SKIP]" in (rules / "conflict-check.md").read_text()

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
        prompt = await assemble_system_prompt(
            agent, project, str(tmp_path), goal_context="", org_context=""
        )
        assert "TestAgent" in prompt
        assert "engineer" in prompt
        assert "TestProject" in prompt
        assert "Writes code" in prompt

    @pytest.mark.asyncio
    async def test_includes_org_and_goal_context(self, tmp_path: Path) -> None:
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
            goal_context="[Goal] Ship MVP",
            org_context="[Mission] Help people",
        )
        assert "[Goal] Ship MVP" in prompt
        assert "[Mission] Help people" in prompt

    @pytest.mark.asyncio
    async def test_includes_active_decisions(self, tmp_path: Path) -> None:
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
            goal_context="", org_context="",
            active_decisions=["녹음 앱으로 방향 확정", "React + FastAPI 기술 스택 확정"],
        )
        assert "녹음 앱으로 방향 확정" in prompt
        assert "React + FastAPI" in prompt
        assert "Active Decisions" in prompt
        assert "Do NOT contradict" in prompt

    @pytest.mark.asyncio
    async def test_reads_rules_from_workspace(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
            goal_context="", org_context="",
        )
        assert "[STATUS]" in prompt
        assert "[DECISION]" in prompt
        assert "[SKIP]" in prompt

    @pytest.mark.asyncio
    async def test_reads_skills_for_capabilities(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        agent = _agent(["plan", "analyze", "coding"])
        prompt = await assemble_system_prompt(
            agent, _project(), str(tmp_path),
            goal_context="", org_context="",
        )
        assert "Skill — Planning" in prompt
        assert "Skill — Codebase Analysis" in prompt
        assert "Skill — Memory Keeping" in prompt
        assert "Skill — Design" not in prompt

    @pytest.mark.asyncio
    async def test_reads_user_custom_rules(self, tmp_path: Path) -> None:
        seed_harness(tmp_path)
        custom = tmp_path / HARNESS_DIR / "rules" / "custom.md"
        custom.write_text("# Custom Rule\nAlways respond in Korean.")
        prompt = await assemble_system_prompt(
            _agent(), _project(), str(tmp_path),
            goal_context="", org_context="",
        )
        assert "Always respond in Korean" in prompt

    @pytest.mark.asyncio
    async def test_includes_team_roster(self, tmp_path: Path) -> None:
        agent1 = _agent()
        agent2 = _agent()
        agent2.name = "Designer"
        agent2.role = "designer"
        agent2.id = uuid.uuid4()
        prompt = await assemble_system_prompt(
            agent1, _project(), str(tmp_path),
            goal_context="", org_context="",
            all_agents=[agent1, agent2],
        )
        assert "@Designer" in prompt

    @pytest.mark.asyncio
    async def test_fallback_when_no_workspace(self) -> None:
        """No workspace_dir → uses inline fallback for everything."""
        prompt = await assemble_system_prompt(
            _agent(), _project(), None,
            goal_context="", org_context="",
        )
        # Should still have communication rules (inline fallback)
        assert "polite language" in prompt.lower() or "professionally" in prompt.lower()
        # Should still have skill fragments (inline fallback)
        assert "Memory Keeping" in prompt


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
    def test_extract_decisions(self) -> None:
        from backend.src.api.agent_chat import _extract_decisions
        text = """
Some text here.
[DECISION] 녹음 앱으로 방향 확정 [/DECISION]
More text.
[DECISION] React + FastAPI 기술 스택 확정 [/DECISION]
"""
        decisions = _extract_decisions(text)
        assert len(decisions) == 2
        assert "녹음 앱으로 방향 확정" in decisions[0]
        assert "React + FastAPI" in decisions[1]

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

    @pytest.mark.asyncio
    async def test_execute_decision_markers_capability_gate(self, tmp_path: Path) -> None:
        """Only agents with 'plan' capability can create decisions."""
        from backend.src.api.agent_chat import _execute_decision_markers

        project = _project()
        project.workspace_dir = str(tmp_path)
        seed_harness(tmp_path)

        # Agent WITHOUT plan capability → decisions ignored
        agent_no_plan = _agent(["coding", "writing"])
        text = "[DECISION] Should be ignored [/DECISION]"
        actions = await _execute_decision_markers(text, project, agent_no_plan)
        assert len(actions) == 0

        # Agent WITH plan capability → decision saved
        agent_with_plan = _agent(["plan", "coding"])
        text = "[DECISION] 녹음 앱으로 방향 확정 [/DECISION]"
        actions = await _execute_decision_markers(text, project, agent_with_plan)
        assert len(actions) == 1
        assert actions[0]["title"] == "녹음 앱으로 방향 확정"

        # Verify file was written
        from backend.src.core.harness import HARNESS_DIR
        decisions_file = tmp_path / HARNESS_DIR / "context" / "decisions.md"
        assert decisions_file.is_file()
        assert "녹음 앱으로 방향 확정" in decisions_file.read_text()

    @pytest.mark.asyncio
    async def test_execute_decision_markers_dedup(self, tmp_path: Path) -> None:
        """Same decision text should not be added twice."""
        from backend.src.api.agent_chat import _execute_decision_markers

        project = _project()
        project.workspace_dir = str(tmp_path)
        seed_harness(tmp_path)
        agent = _agent(["plan"])

        text = "[DECISION] 녹음 앱 확정 [/DECISION]"
        await _execute_decision_markers(text, project, agent)
        actions = await _execute_decision_markers(text, project, agent)
        assert len(actions) == 0  # duplicate, not added again


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

    @pytest.mark.asyncio
    async def test_execute_create_phase_markers(self, db_session) -> None:
        """Phase markers create Phase rows in the DB."""
        from backend.src.api.agent_chat import _execute_create_phase_markers
        from backend.src.core.tenant_context import DEFAULT_TENANT_ID
        from backend.src.models import Phase, Project, ProjectStatus, Tenant

        db_session.add(Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="test"))
        project = Project(id=uuid.uuid4(), name="P", description="", status=ProjectStatus.active)
        db_session.add(project)
        await db_session.commit()

        text = '[CREATE_PHASE]{"name": "Research", "description": "Market analysis", "status": "active"}[/CREATE_PHASE]'
        actions = await _execute_create_phase_markers(text, project.id, db_session)
        assert len(actions) == 1
        assert actions[0]["type"] == "phase_created"
        assert actions[0]["title"] == "Research"

        # Dedup: same name again → no new phase
        actions2 = await _execute_create_phase_markers(text, project.id, db_session)
        assert len(actions2) == 0
