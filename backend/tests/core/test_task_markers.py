"""Tests for inline marker parsing in task_markers.py.

TDD RED phase — these tests define the expected behavior for
[CREATE_TASK ...] and [CREATE_PHASE ...] inline markers.
"""

import pytest

from backend.src.core.task_markers import (
    parse_inline_phase_markers,
    parse_inline_task_markers,
    strip_action_markers,
    strip_inline_markers,
)


# ── parse_inline_task_markers ─────────────────────────────────────


class TestParseInlineTaskMarkers:
    def test_single_task(self):
        text = '[CREATE_TASK title="로그인 화면 디자인" assignee="Designer"]'
        result = parse_inline_task_markers(text)
        assert len(result) == 1
        assert result[0]["title"] == "로그인 화면 디자인"
        assert result[0]["assignee"] == "Designer"

    def test_task_with_all_attrs(self):
        text = '[CREATE_TASK title="백엔드 API" assignee="CTO" priority="high" task_type="feature" phase_name="개발" description="REST API 구현"]'
        result = parse_inline_task_markers(text)
        assert len(result) == 1
        assert result[0]["title"] == "백엔드 API"
        assert result[0]["assignee"] == "CTO"
        assert result[0]["priority"] == "high"
        assert result[0]["task_type"] == "feature"
        assert result[0]["phase_name"] == "개발"
        assert result[0]["description"] == "REST API 구현"

    def test_task_title_only(self):
        text = '[CREATE_TASK title="간단한 작업"]'
        result = parse_inline_task_markers(text)
        assert len(result) == 1
        assert result[0]["title"] == "간단한 작업"
        assert result[0].get("assignee") is None
        assert result[0].get("priority") is None

    def test_multiple_tasks(self):
        text = """방금 작업을 만들었습니다.
[CREATE_TASK title="화면 디자인" assignee="Designer"]
[CREATE_TASK title="API 개발" assignee="CTO" priority="high"]
[CREATE_TASK title="테스트 작성" assignee="QA"]
다음 단계로 넘어갑시다."""
        result = parse_inline_task_markers(text)
        assert len(result) == 3
        assert result[0]["title"] == "화면 디자인"
        assert result[1]["title"] == "API 개발"
        assert result[1]["priority"] == "high"
        assert result[2]["title"] == "테스트 작성"

    def test_no_markers(self):
        text = "이건 일반 텍스트입니다. 마커가 없어요."
        result = parse_inline_task_markers(text)
        assert result == []

    def test_empty_string(self):
        result = parse_inline_task_markers("")
        assert result == []

    def test_title_with_escaped_quotes(self):
        text = r'[CREATE_TASK title="API \"v2\" 개발" assignee="CTO"]'
        result = parse_inline_task_markers(text)
        assert len(result) == 1
        assert result[0]["title"] == 'API "v2" 개발'

    def test_attrs_any_order(self):
        text = '[CREATE_TASK assignee="Designer" priority="low" title="작업"]'
        result = parse_inline_task_markers(text)
        assert len(result) == 1
        assert result[0]["title"] == "작업"
        assert result[0]["assignee"] == "Designer"
        assert result[0]["priority"] == "low"

    def test_mixed_with_prose(self):
        text = """안녕하세요! 프로젝트 계획을 세웠습니다.
[CREATE_TASK title="시장 조사" assignee="CMO"]
CMO님이 시장 조사를 먼저 진행해주세요.
[CREATE_TASK title="기술 스택 선정" assignee="CTO"]
@CTO 기술 스택을 잡아주세요."""
        result = parse_inline_task_markers(text)
        assert len(result) == 2
        assert result[0]["title"] == "시장 조사"
        assert result[1]["title"] == "기술 스택 선정"

    def test_ignores_phase_markers(self):
        text = """[CREATE_PHASE name="기획"]
[CREATE_TASK title="작업1" assignee="CTO"]"""
        result = parse_inline_task_markers(text)
        assert len(result) == 1
        assert result[0]["title"] == "작업1"

    def test_ignores_old_block_markers(self):
        text = """[CREATE_TASK]old block style[/CREATE_TASK]
[CREATE_TASK title="새로운 방식"]"""
        result = parse_inline_task_markers(text)
        assert len(result) == 1
        assert result[0]["title"] == "새로운 방식"


# ── parse_inline_phase_markers ────────────────────────────────────


class TestParseInlinePhaseMarkers:
    def test_single_phase(self):
        text = '[CREATE_PHASE name="프론트엔드 개발" description="UI 구현"]'
        result = parse_inline_phase_markers(text)
        assert len(result) == 1
        assert result[0]["name"] == "프론트엔드 개발"
        assert result[0]["description"] == "UI 구현"

    def test_phase_name_only(self):
        text = '[CREATE_PHASE name="백엔드"]'
        result = parse_inline_phase_markers(text)
        assert len(result) == 1
        assert result[0]["name"] == "백엔드"
        assert result[0].get("description") is None

    def test_multiple_phases(self):
        text = """[CREATE_PHASE name="기획" description="시장 조사 및 기획"]
[CREATE_PHASE name="개발" description="구현"]"""
        result = parse_inline_phase_markers(text)
        assert len(result) == 2
        assert result[0]["name"] == "기획"
        assert result[1]["name"] == "개발"

    def test_no_markers(self):
        result = parse_inline_phase_markers("일반 텍스트")
        assert result == []

    def test_ignores_task_markers(self):
        text = """[CREATE_TASK title="작업"]
[CREATE_PHASE name="단계"]"""
        result = parse_inline_phase_markers(text)
        assert len(result) == 1
        assert result[0]["name"] == "단계"


# ── strip_inline_markers ─────────────────────────────────────────


class TestStripInlineMarkers:
    def test_strips_task_marker(self):
        text = 'Hello\n[CREATE_TASK title="작업" assignee="CTO"]\nWorld'
        result = strip_inline_markers(text)
        assert "[CREATE_TASK" not in result
        assert "Hello" in result
        assert "World" in result

    def test_strips_phase_marker(self):
        text = '[CREATE_PHASE name="기획"]\n계획을 세웠습니다.'
        result = strip_inline_markers(text)
        assert "[CREATE_PHASE" not in result
        assert "계획을 세웠습니다." in result

    def test_strips_mixed_markers(self):
        text = """시작합니다.
[CREATE_PHASE name="기획"]
[CREATE_TASK title="작업1" assignee="CTO"]
[CREATE_TASK title="작업2" assignee="Designer"]
완료했습니다."""
        result = strip_inline_markers(text)
        assert "[CREATE_PHASE" not in result
        assert "[CREATE_TASK" not in result
        assert "시작합니다." in result
        assert "완료했습니다." in result

    def test_no_markers_unchanged(self):
        text = "일반 텍스트 메시지"
        assert strip_inline_markers(text) == text


# ── strip_action_markers includes inline ──────────────────────────


class TestStripActionMarkersIncludesInline:
    def test_strips_both_old_and_new(self):
        text = """[CREATE_TASK]old style[/CREATE_TASK]
[CREATE_TASK title="new style"]
[CREATE_PHASE]old phase[/CREATE_PHASE]
[CREATE_PHASE name="new phase"]
일반 텍스트"""
        result = strip_action_markers(text)
        assert "[CREATE_TASK" not in result
        assert "[CREATE_PHASE" not in result
        assert "일반 텍스트" in result
