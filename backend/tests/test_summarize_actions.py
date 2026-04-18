"""Tests for _summarize_actions fallback when CEO returns tool-only response."""
from __future__ import annotations

from backend.src.api.agent_chat import _summarize_actions, _looks_like_tool_call_json


def test_looks_like_tool_call_json_detects_function_dump():
    text = '{"id": "call_abc", "function": {"name": "create_task", "arguments": {"title": "x"}}}'
    assert _looks_like_tool_call_json(text) is True


def test_looks_like_tool_call_json_detects_task_result():
    text = '{"task_id": "abc-123", "status": "done"}'
    assert _looks_like_tool_call_json(text) is True


def test_looks_like_tool_call_json_ignores_natural_language():
    assert _looks_like_tool_call_json("방금 아키텍처 문서를 작성했습니다. @Designer에게 넘깁니다.") is False
    assert _looks_like_tool_call_json("") is False
    assert _looks_like_tool_call_json("Hello @team!") is False


def test_looks_like_tool_call_json_ignores_markdown_json_mention():
    # Paragraph that happens to mention "status" as prose — not a JSON dump
    assert _looks_like_tool_call_json("Here is the task status: done") is False


def test_looks_like_tool_call_json_catches_create_screen_payload_leak():
    # Regression: Designer sometimes dumps create_screen tool args as the
    # chat body instead of using the tool_calls slot.
    payload = (
        '{"name": "User Dashboard Overview", "route": "/dashboard", '
        '"intent": "Main user dashboard screen showing key metrics", '
        '"spec": {"type": "Screen", "children": [{"type": "View"}]}}'
    )
    assert _looks_like_tool_call_json(payload) is True


def test_looks_like_tool_call_json_catches_file_write_payload_leak():
    # file_write arguments dumped as text instead of sent as a tool_call.
    payload = '{"path": "src/server.js", "content": "const express = require(\\"express\\");"}'
    assert _looks_like_tool_call_json(payload) is True


def test_looks_like_tool_call_json_catches_list_tasks_result_leak():
    payload = '{"tasks": [{"id": "abc", "title": "x"}], "total": 1}'
    assert _looks_like_tool_call_json(payload) is True


def test_looks_like_tool_call_json_ignores_short_json_like_prose():
    # A user might quote a small inline JSON snippet — we shouldn't swallow it.
    assert _looks_like_tool_call_json("파일 이름은 `{name}` 형식이어야 해요.") is False


def test_summarize_create_tasks_and_phase():
    actions = [
        {"tool": "create_phase", "input": {"name": "Product Planning"}},
        {"tool": "create_task", "input": {"title": "Conduct user research", "assignee": "Product_Manager"}},
        {"tool": "create_task", "input": {"title": "Define core features", "assignee": "Product_Manager"}},
    ]
    summary = _summarize_actions(actions)
    assert "Product Planning" in summary
    assert "작업 2개" in summary
    assert "Conduct user research" in summary
    assert "Define core features" in summary
    # Assignees should be mentioned so chat reads as a handoff
    assert "@Product_Manager" in summary


def test_summarize_mentions_multiple_unique_assignees():
    actions = [
        {"tool": "create_phase", "input": {"name": "Kickoff"}},
        {"tool": "create_task", "input": {"title": "t1", "assignee": "CTO"}},
        {"tool": "create_task", "input": {"title": "t2", "assignee": "Designer"}},
        {"tool": "create_task", "input": {"title": "t3", "assignee": "CTO"}},
    ]
    summary = _summarize_actions(actions)
    assert "@CTO" in summary
    assert "@Designer" in summary
    # Should not double-mention CTO
    assert summary.count("@CTO") == 1


def test_summarize_file_writes_conversational():
    actions = [
        {"tool": "claim_task", "input": {"task_id": "abc"}},
        {"tool": "file_write", "input": {"path": "docs/architecture.md"}},
        {"tool": "complete_task", "input": {"task_id": "abc", "summary": "Architecture doc authored"}},
    ]
    summary = _summarize_actions(actions)
    assert "docs/architecture.md" in summary
    assert "작업 완료" in summary
    assert "Architecture doc authored" in summary


def test_summarize_multiple_files():
    actions = [
        {"tool": "file_write", "input": {"path": "src/a.js"}},
        {"tool": "file_write", "input": {"path": "src/b.js"}},
    ]
    summary = _summarize_actions(actions)
    assert "src/a.js" in summary and "src/b.js" in summary


def test_summarize_preserves_mentions_from_completion_summary():
    """Passive agents often write @mentions inside complete_task.summary.
    The fallback message must surface them so ping-pong delegation can wake them."""
    actions = [
        {"tool": "claim_task", "input": {"task_id": "x"}},
        {"tool": "file_write", "input": {"path": "src/api.js"}},
        {"tool": "complete_task", "input": {
            "task_id": "x",
            "summary": "API 뼈대 완성. @Frontend_Engineer 붙여주세요. @QA 테스트도 필요합니다.",
        }},
    ]
    summary = _summarize_actions(actions)
    # Mentions must appear somewhere in the output so the delegation parser can pick them up
    assert "@Frontend_Engineer" in summary
    assert "@QA" in summary
    # Trailing handoff line consolidates mentions (may also appear verbatim inside the summary text)
    assert "이어서 부탁드립니다" in summary


def test_summarize_blocked_tasks():
    actions = [
        {"tool": "update_task", "input": {"task_id": "x", "status": "blocked"}},
    ]
    summary = _summarize_actions(actions)
    assert "blocked" in summary.lower() or "막혀" in summary


def test_summarize_empty_actions():
    assert _summarize_actions([]) == ""


def test_summarize_truncates_long_title_lists():
    actions = [
        {"tool": "create_task", "input": {"title": f"Task {i}"}}
        for i in range(10)
    ]
    summary = _summarize_actions(actions)
    assert "작업 10개" in summary
    assert "Task 0" in summary
    # Only first 6 titles listed, rest collapsed
    assert "외 4건" in summary


def test_summarize_unknown_tool_fallback():
    actions = [
        {"tool": "set_goal", "input": {"title": "Some goal"}},
    ]
    summary = _summarize_actions(actions)
    assert "set_goal" in summary


def test_summarize_empty_actions_returns_empty():
    assert _summarize_actions([]) == ""
