"""``parse_verification_block`` — fenced JSON block extraction + cwd resolution."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.src.core.verification_parser import (
    parse_verification_block,
    resolve_workspace_cwd,
    strip_verification_blocks,
)
from backend.src.core.verifier.protocol import VerifierType


# ─── Happy path ────────────────────────────────────────────────────


def test_parse_extracts_verifier_type_and_command_from_fenced_block() -> None:
    reply = """\
Done — wrote add.py and tests/test_add.py. shell_exec gave exit=0.

```bsnexus-verification
{
  "verifier_type": "software_test",
  "command": ["python", "-m", "pytest", "tests/test_add.py", "-q"],
  "cwd": "tests",
  "timeout_s": 60
}
```
"""
    parsed = parse_verification_block(reply)
    assert parsed is not None
    assert parsed.verifier_type == VerifierType.software_test
    assert parsed.inputs["command"] == [
        "python",
        "-m",
        "pytest",
        "tests/test_add.py",
        "-q",
    ]
    assert parsed.inputs["cwd"] == "tests"
    assert parsed.inputs["timeout_s"] == 60


def test_parse_picks_first_fenced_block_when_multiple_present() -> None:
    """LLM rambles and emits two blocks; we take the first to keep the
    contract closed (the worker isn't free to splice across multiple
    proposals)."""
    reply = """
```bsnexus-verification
{"verifier_type": "software_test", "command": ["pytest"]}
```

later thoughts:

```bsnexus-verification
{"verifier_type": "software_build", "command": ["pnpm", "build"]}
```
"""
    parsed = parse_verification_block(reply)
    assert parsed is not None
    assert parsed.verifier_type == VerifierType.software_test
    assert parsed.inputs["command"] == ["pytest"]


def test_parse_accepts_string_command_returning_single_arg_list() -> None:
    reply = '```bsnexus-verification\n{"verifier_type": "software_test", "command": "pytest"}\n```'
    parsed = parse_verification_block(reply)
    assert parsed is not None
    assert parsed.inputs["command"] == ["pytest"]


def test_parse_extracts_optional_env_dict() -> None:
    reply = """```bsnexus-verification
{"verifier_type": "software_test", "command": ["pytest"], "env": {"PYTHONPATH": "src", "DEBUG": 1}}
```"""
    parsed = parse_verification_block(reply)
    assert parsed is not None
    assert parsed.inputs["env"] == {"PYTHONPATH": "src", "DEBUG": "1"}


# ─── Fail-soft branches — every one must return None, never raise ──


def test_parse_returns_none_when_block_missing() -> None:
    assert parse_verification_block("just a regular reply") is None


def test_parse_returns_none_for_empty_text() -> None:
    assert parse_verification_block("") is None


def test_parse_returns_none_for_invalid_json() -> None:
    reply = "```bsnexus-verification\n{not valid json}\n```"
    assert parse_verification_block(reply) is None


def test_parse_returns_none_for_unknown_verifier_type() -> None:
    reply = (
        "```bsnexus-verification\n"
        '{"verifier_type": "design_screenshot", "command": ["pytest"]}\n```'
    )
    assert parse_verification_block(reply) is None


def test_parse_returns_none_when_command_missing() -> None:
    reply = '```bsnexus-verification\n{"verifier_type": "software_test"}\n```'
    assert parse_verification_block(reply) is None


def test_parse_returns_none_for_command_list_with_non_strings() -> None:
    reply = (
        "```bsnexus-verification\n"
        '{"verifier_type": "software_test", "command": ["pytest", 1]}\n```'
    )
    assert parse_verification_block(reply) is None


def test_parse_returns_none_for_top_level_array_payload() -> None:
    reply = '```bsnexus-verification\n["pytest"]\n```'
    assert parse_verification_block(reply) is None


# ─── resolve_workspace_cwd ─────────────────────────────────────────


def test_resolve_relative_cwd_joins_to_workspace_root(tmp_path: Path) -> None:
    pid = uuid.uuid4()
    out = resolve_workspace_cwd(
        {"command": ["pytest"], "cwd": "tests"},
        project_id=pid,
        workspace_root=tmp_path,
    )
    assert out["cwd"] == str((tmp_path / "tests").resolve())


def test_resolve_missing_cwd_defaults_to_workspace_root(tmp_path: Path) -> None:
    pid = uuid.uuid4()
    out = resolve_workspace_cwd(
        {"command": ["pytest"]},
        project_id=pid,
        workspace_root=tmp_path,
    )
    assert out["cwd"] == str(tmp_path.resolve())


def test_resolve_absolute_cwd_falls_back_to_workspace_root(tmp_path: Path) -> None:
    pid = uuid.uuid4()
    out = resolve_workspace_cwd(
        {"command": ["pytest"], "cwd": "/etc"},
        project_id=pid,
        workspace_root=tmp_path,
    )
    assert out["cwd"] == str(tmp_path.resolve())


def test_resolve_escaping_cwd_falls_back_to_workspace_root(tmp_path: Path) -> None:
    pid = uuid.uuid4()
    out = resolve_workspace_cwd(
        {"command": ["pytest"], "cwd": "../../../etc"},
        project_id=pid,
        workspace_root=tmp_path,
    )
    assert out["cwd"] == str(tmp_path.resolve())


@pytest.mark.parametrize("dot", [".", "./"])
def test_resolve_dot_cwd_treated_as_workspace_root(tmp_path: Path, dot: str) -> None:
    pid = uuid.uuid4()
    out = resolve_workspace_cwd(
        {"command": ["pytest"], "cwd": dot},
        project_id=pid,
        workspace_root=tmp_path,
    )
    assert out["cwd"] == str(tmp_path.resolve())


# ─── strip_verification_blocks ────────────────────────────────────


def test_strip_removes_single_fenced_block() -> None:
    """The block is server-side metadata; downstream consumers (chat
    surface, deliverable title) must not see it. Live-LLM run on
    2026-05-08 reproduced the leak — title became literally
    "bsnexus-verification" because ``_first_sentence`` walked into the
    fenced block when the LLM emitted no other prose. This regression
    test guards the fix."""
    reply = """\
done — wrote add.py + tests/test_add.py.

```bsnexus-verification
{"verifier_type": "software_test", "command": ["pytest"]}
```
"""
    cleaned = strip_verification_blocks(reply)
    assert "bsnexus-verification" not in cleaned
    assert "verifier_type" not in cleaned
    assert "done — wrote add.py + tests/test_add.py." in cleaned


def test_strip_removes_all_blocks_when_multiple_present() -> None:
    reply = """\
```bsnexus-verification
{"verifier_type": "software_test", "command": ["pytest"]}
```

middle prose

```bsnexus-verification
{"verifier_type": "software_build", "command": ["pnpm", "build"]}
```
"""
    cleaned = strip_verification_blocks(reply)
    assert "bsnexus-verification" not in cleaned
    assert "middle prose" in cleaned


def test_strip_returns_input_unchanged_when_no_block() -> None:
    reply = "just a regular reply with code:\n```python\nprint('hi')\n```"
    cleaned = strip_verification_blocks(reply)
    assert cleaned == reply.strip()


def test_strip_handles_empty_input() -> None:
    assert strip_verification_blocks("") == ""
