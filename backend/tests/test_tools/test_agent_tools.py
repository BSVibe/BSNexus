"""Tests for tools/agent_tools.py — capability-to-tool mapping."""

from __future__ import annotations

from backend.src.tools.agent_tools import UNIVERSAL_TOOLS, get_tools_for_agent


class TestGetToolsForAgent:
    def test_no_capabilities_returns_universal(self) -> None:
        tools = get_tools_for_agent(None)
        names = {t.name for t in tools}
        assert set(UNIVERSAL_TOOLS).issubset(names)

    def test_empty_capabilities_returns_universal(self) -> None:
        tools = get_tools_for_agent([])
        names = {t.name for t in tools}
        assert set(UNIVERSAL_TOOLS).issubset(names)

    def test_plan_capability_includes_create_phase(self) -> None:
        tools = get_tools_for_agent(["plan"])
        names = {t.name for t in tools}
        assert "create_phase" in names
        assert "set_goal" in names
        assert "record_decision" in names

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
        assert "claim_task" in names

    def test_multiple_capabilities_union(self) -> None:
        tools = get_tools_for_agent(["plan", "design"])
        names = {t.name for t in tools}
        # plan tools
        assert "create_phase" in names
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
