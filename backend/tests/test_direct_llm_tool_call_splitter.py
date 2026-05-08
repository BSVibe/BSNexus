"""Backstop for the Ollama streaming concatenation that 2026-05-08
live-LLM dogfood surfaced — ``_split_concatenated_tool_call_arguments``."""

from __future__ import annotations

import json

from backend.src.core.llm.direct_client import _split_concatenated_tool_call_arguments


def _tc(id_: str, name: str, arguments: str) -> dict:
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_passes_through_clean_single_argument_json() -> None:
    tcs = [_tc("call-1", "file_write", '{"path": "a.py", "content": "x"}')]
    out = _split_concatenated_tool_call_arguments(tcs)
    assert out == tcs


def test_passes_through_empty_arguments() -> None:
    tcs = [_tc("call-1", "noop", "")]
    out = _split_concatenated_tool_call_arguments(tcs)
    assert out == tcs


def test_splits_two_json_objects_into_two_tool_calls() -> None:
    concatenated = '{"path": "add.py", "content": "x"}{"path": "tests/test_add.py", "content": "y"}'
    tcs = [_tc("call-A", "file_write", concatenated)]
    out = _split_concatenated_tool_call_arguments(tcs)

    assert len(out) == 2
    assert json.loads(out[0]["function"]["arguments"]) == {"path": "add.py", "content": "x"}
    assert json.loads(out[1]["function"]["arguments"]) == {
        "path": "tests/test_add.py",
        "content": "y",
    }
    # IDs preserved on first, suffixed on follow-ons so tool_result
    # round-trip in the next round addresses the right call.
    assert out[0]["id"] == "call-A"
    assert out[1]["id"] == "call-A-1"
    # Function name preserved.
    assert all(c["function"]["name"] == "file_write" for c in out)


def test_splits_three_objects_with_whitespace_between() -> None:
    concatenated = '{"a": 1}\n{"b": 2}  {"c": 3}'
    tcs = [_tc("call-1", "f", concatenated)]
    out = _split_concatenated_tool_call_arguments(tcs)
    assert len(out) == 3
    assert [json.loads(c["function"]["arguments"]) for c in out] == [
        {"a": 1},
        {"b": 2},
        {"c": 3},
    ]


def test_keeps_clean_call_when_other_call_needs_split() -> None:
    """Heterogeneous list — split only the broken slot, leave the
    others untouched."""
    tcs = [
        _tc("clean", "f", '{"x": 1}'),
        _tc("dirty", "g", '{"a": 1}{"b": 2}'),
    ]
    out = _split_concatenated_tool_call_arguments(tcs)
    assert len(out) == 3
    assert out[0]["id"] == "clean"
    assert out[1]["id"] == "dirty"
    assert out[2]["id"] == "dirty-1"


def test_drops_unparseable_remainder_after_first_object() -> None:
    """Final tail isn't valid JSON — keep what we recovered, log
    discarded remainder. Better than crashing the run."""
    concatenated = '{"a": 1}garbage'
    tcs = [_tc("call-1", "f", concatenated)]
    out = _split_concatenated_tool_call_arguments(tcs)
    assert len(out) == 1
    assert json.loads(out[0]["function"]["arguments"]) == {"a": 1}


def test_empty_list_passthrough() -> None:
    assert _split_concatenated_tool_call_arguments([]) == []
