"""Tests for tools/agent_tools.py — capability-to-tool mapping."""

from __future__ import annotations

from backend.src.tools.agent_tools import UNIVERSAL_TOOLS, get_tools_for_agent, get_tools_for_mode


class TestGetToolsForAgent:
    def test_no_capabilities_returns_universal(self) -> None:
        tools = get_tools_for_agent(None)
        names = {t.name for t in tools}
        assert set(UNIVERSAL_TOOLS).issubset(names)

    def test_empty_capabilities_returns_universal(self) -> None:
        tools = get_tools_for_agent([])
        names = {t.name for t in tools}
        assert set(UNIVERSAL_TOOLS).issubset(names)

    def test_plan_capability_includes_planning_tools(self) -> None:
        """Plan capability: set_goal, record_decision (create_task/phase via markers)."""
        tools = get_tools_for_agent(["plan"])
        names = {t.name for t in tools}
        assert "set_goal" in names
        assert "record_decision" in names
        # create_task/create_phase removed — handled by inline markers
        assert "create_task" not in names
        assert "create_phase" not in names

    def test_design_capability_includes_screen_tools(self) -> None:
        tools = get_tools_for_agent(["design"])
        names = {t.name for t in tools}
        assert "create_screen" in names
        assert "modify_screen" in names
        assert "file_write" in names

    def test_coding_capability_includes_file_tools(self) -> None:
        tools = get_tools_for_agent(["coding"])
        names = {t.name for t in tools}
        assert "file_read" in names
        assert "file_write" in names
        # claim_task/complete_task removed — handled by inline markers
        assert "claim_task" not in names

    def test_multiple_capabilities_union(self) -> None:
        tools = get_tools_for_agent(["plan", "design"])
        names = {t.name for t in tools}
        # plan tools
        assert "set_goal" in names
        # design tools
        assert "create_screen" in names
        assert "modify_screen" in names

    def test_unknown_capability_only_universal(self) -> None:
        tools = get_tools_for_agent(["unknown_cap"])
        names = {t.name for t in tools}
        assert names == set(UNIVERSAL_TOOLS)

    def test_returns_tool_instances(self) -> None:
        tools = get_tools_for_agent(["plan"])
        for t in tools:
            assert hasattr(t, "execute")
            assert hasattr(t, "to_definition")


class TestGetToolsForMode:
    """Mode-based tool filtering — active has no creation/write tools, only
    file_read for .bsnexus/context/*.md. Passive has execution tools."""

    def test_passive_mode_includes_file_write_and_read(self) -> None:
        tools = get_tools_for_mode("passive", [])
        names = {t.name for t in tools}
        assert "file_write" in names
        assert "file_read" in names

    def test_passive_design_capability_adds_screen_tools(self) -> None:
        tools = get_tools_for_mode("passive", ["design"])
        names = {t.name for t in tools}
        assert "create_screen" in names
        assert "modify_screen" in names

    def test_active_mode_has_no_tools(self) -> None:
        """Active mode ships with ZERO tools. Both Qwen3-coder:30b and
        GLM-4.7-flash were observed to loop on any tool we offered
        (file_read×7-10 with no text reply). Current plan state is
        inlined into the system prompt instead so the agent can reason
        against it directly — no tool call needed."""
        tools = get_tools_for_mode("active", ["plan", "design"])
        names = {t.name for t in tools}
        assert names == set()
