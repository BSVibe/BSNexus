"""G10 — tests for ``decompose_request``.

The decomposer is one ExecutorClient call: the work LLM looks at the
ProjectContext and replies with a JSON array of step drafts. We cover
the path matrix without standing up a real model:

- simple Request → 1 step (LLM honors "return one step if simple")
- sequential Request → 2-4 steps (LLM honors "natural checkpoints")
- LLM raises → fall back to one step (Request title as step)
- malformed JSON → one retry → still bad → fallback
- 8 steps returned → truncated to 6 with last step marked
- empty/whitespace LLM output → fallback

Stub executors mimic the BSGateway result shape (``output_ref`` carries
plain text when ``tool_calls`` is None) — same approach as the
existing G9 orchestration tests.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.src.core.planning.context import ProjectContext
from backend.src.core.planning.decomposer import (
    MAX_STEPS,
    decompose_request,
)


@dataclass
class _StubLLM:
    """Returns ``responses[i]`` on the i-th call. ``side_effect`` runs
    instead if set."""

    responses: list[str] = field(default_factory=list)
    side_effect: Exception | None = None
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(messages)
        if self.side_effect is not None:
            raise self.side_effect
        text = self.responses.pop(0) if self.responses else ""
        return {
            "output_type": "text",
            "output_ref": text,
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


def _ctx(intent: str) -> ProjectContext:
    return ProjectContext(request_intent=intent)


@pytest.mark.asyncio
async def test_decompose_simple_request_returns_one_step() -> None:
    llm = _StubLLM(
        responses=[
            json.dumps(
                [
                    {
                        "name": "Add /healthz",
                        "objective": "Implement a tiny health endpoint.",
                        "expected_outputs": ["src/api/health.py"],
                    }
                ]
            )
        ]
    )
    steps = await decompose_request(_ctx("Add /healthz endpoint"), executor=llm, model="m")
    assert len(steps) == 1
    assert steps[0].name == "Add /healthz"
    assert steps[0].expected_outputs == ["src/api/health.py"]
    # One LLM call, no retry.
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_decompose_sequential_request_returns_multiple_steps() -> None:
    llm = _StubLLM(
        responses=[
            json.dumps(
                [
                    {"name": "Schema", "objective": "Define schema.", "expected_outputs": ["schema.py"]},
                    {"name": "API", "objective": "Wire endpoints.", "expected_outputs": ["api.py"]},
                    {"name": "UI", "objective": "Add the page.", "expected_outputs": ["ui.tsx"]},
                ]
            )
        ]
    )
    steps = await decompose_request(_ctx("Build a task tracker (schema + API + UI)"), executor=llm, model="m")
    assert [s.name for s in steps] == ["Schema", "API", "UI"]


@pytest.mark.asyncio
async def test_decompose_llm_raises_falls_back_to_single_step() -> None:
    llm = _StubLLM(side_effect=RuntimeError("LLM down"))
    intent = "Anything"
    steps = await decompose_request(_ctx(intent), executor=llm, model="m")
    assert len(steps) == 1
    assert intent in steps[0].objective


@pytest.mark.asyncio
async def test_decompose_malformed_json_retries_then_falls_back() -> None:
    llm = _StubLLM(responses=["not json at all", "still {{ broken"])
    intent = "Do a thing"
    steps = await decompose_request(_ctx(intent), executor=llm, model="m")
    # First call + one retry = 2 LLM calls, then single-step fallback.
    assert len(llm.calls) == 2
    assert len(steps) == 1
    assert intent in steps[0].objective


@pytest.mark.asyncio
async def test_decompose_malformed_then_valid_json_returns_parsed_plan() -> None:
    """Retry on parse failure must succeed when the second reply is valid."""
    llm = _StubLLM(
        responses=[
            "not json at all",
            json.dumps([{"name": "Step", "objective": "Do it.", "expected_outputs": []}]),
        ]
    )
    steps = await decompose_request(_ctx("Intent"), executor=llm, model="m")
    assert len(llm.calls) == 2
    assert len(steps) == 1
    assert steps[0].name == "Step"


@pytest.mark.asyncio
async def test_decompose_truncates_to_max_steps_and_marks_last() -> None:
    too_many = [{"name": f"step-{i}", "objective": f"obj-{i}", "expected_outputs": []} for i in range(MAX_STEPS + 2)]
    llm = _StubLLM(responses=[json.dumps(too_many)])
    steps = await decompose_request(_ctx("Mega request"), executor=llm, model="m")
    assert len(steps) == MAX_STEPS
    assert "follow-up split required" in steps[-1].name.lower()


@pytest.mark.asyncio
async def test_decompose_empty_text_falls_back() -> None:
    llm = _StubLLM(responses=["", "   "])
    intent = "Anything"
    steps = await decompose_request(_ctx(intent), executor=llm, model="m")
    assert len(steps) == 1
    assert intent in steps[0].objective


@pytest.mark.asyncio
async def test_decompose_extracts_json_from_fenced_block() -> None:
    """Models often wrap JSON in ```json fences. The decomposer should
    survive that without a custom system-prompt round-trip."""
    fenced = (
        "Sure, here is the plan:\n\n"
        "```json\n" + json.dumps([{"name": "Only step", "objective": "Do it.", "expected_outputs": ["x.py"]}]) + "\n```"
    )
    llm = _StubLLM(responses=[fenced])
    steps = await decompose_request(_ctx("Trivial"), executor=llm, model="m")
    assert len(llm.calls) == 1
    assert len(steps) == 1
    assert steps[0].name == "Only step"


@pytest.mark.asyncio
async def test_decompose_skips_invalid_step_entries() -> None:
    """Entries missing required fields are dropped instead of crashing
    the whole plan. If every entry is bad we fall back to single-step."""
    llm = _StubLLM(
        responses=[
            json.dumps(
                [
                    {"name": "ok", "objective": "obj", "expected_outputs": []},
                    {"objective": "no name"},  # invalid
                    {"name": "", "objective": "empty name"},  # invalid
                ]
            )
        ]
    )
    steps = await decompose_request(_ctx("Intent"), executor=llm, model="m")
    assert len(steps) == 1
    assert steps[0].name == "ok"


@pytest.mark.asyncio
async def test_decompose_all_invalid_entries_falls_back() -> None:
    llm = _StubLLM(
        responses=[
            json.dumps([{"objective": "no name"}, {"name": ""}]),
        ]
    )
    intent = "Mystery work"
    steps = await decompose_request(_ctx(intent), executor=llm, model="m")
    assert len(steps) == 1
    assert intent in steps[0].objective
