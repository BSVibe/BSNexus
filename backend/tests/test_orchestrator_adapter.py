"""LiteLLMOrchestratorAdapter — tool-calling loop over litellm.acompletion."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.src.core.orchestrator_adapter import LiteLLMOrchestratorAdapter


def _fake_response(
    content: str | None = None,
    *,
    tool_calls: list[dict] | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
):
    """Build a fake ``litellm.acompletion`` response.

    Tool-call dicts have shape ``{"id","function":{"name","arguments"}}``.
    """
    message = SimpleNamespace(
        content=content,
        tool_calls=[
            SimpleNamespace(
                id=tc["id"],
                type="function",
                function=SimpleNamespace(
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                ),
            )
            for tc in (tool_calls or [])
        ]
        or None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


@pytest.mark.asyncio
async def test_adapter_returns_orchestrator_shaped_dict_no_tools():
    adapter = LiteLLMOrchestratorAdapter(
        model="ollama/glm-4.7-flash:latest",
        project_id=uuid.uuid4(),
        api_key="unused",
        base_url="http://localhost:11434",
    )

    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            AsyncMock(return_value=_fake_response(content="hello world", prompt_tokens=40, completion_tokens=12)),
        ) as mock_call,
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            return_value=(0.001, 0.001),
        ),
    ):
        out = await adapter.execute(
            "You are a helpful assistant.",
            "Task: greet.",
            tools_allowed=["file_read"],
        )

    mock_call.assert_awaited_once()
    kwargs = mock_call.await_args.kwargs
    assert kwargs["model"] == "ollama/glm-4.7-flash:latest"
    assert kwargs["api_base"] == "http://localhost:11434"
    # Ollama models get a larger num_ctx hint so local backends don't truncate.
    assert "num_ctx" in kwargs
    # Tool schema should have been passed through.
    assert [t["function"]["name"] for t in kwargs["tools"]] == ["file_read"]
    assert kwargs["tool_choice"] == "auto"

    assert out["status"] == "done"
    assert out["output_type"] == "text"
    assert out["output_ref"] == {"inline": "hello world", "files": []}
    assert out["prompt_tokens"] == 40
    assert out["completion_tokens"] == 12
    # (0.001 + 0.001) USD × 100 = 0.2 cents → rounds to 0.
    assert out["actual_cost_cents"] == 0


@pytest.mark.asyncio
async def test_adapter_runs_tool_loop_and_records_file_writes(tmp_path, monkeypatch):
    project_id = uuid.uuid4()
    monkeypatch.setattr(
        "backend.src.core.project_workspace._root",
        lambda: tmp_path,
    )
    adapter = LiteLLMOrchestratorAdapter(model="openai/gpt-4o", project_id=project_id)

    # Turn 1: model asks to write a file.
    # Turn 2: model replies with final prose (no tool calls).
    responses = [
        _fake_response(
            tool_calls=[
                {
                    "id": "call-1",
                    "function": {
                        "name": "file_write",
                        "arguments": json.dumps(
                            {
                                "path": "hello.txt",
                                "content": "hi from the agent",
                                "language": "text",
                            }
                        ),
                    },
                }
            ],
            prompt_tokens=10,
            completion_tokens=5,
        ),
        _fake_response(
            content="Done — wrote hello.txt.",
            prompt_tokens=20,
            completion_tokens=8,
        ),
    ]
    acompletion_mock = AsyncMock(side_effect=responses)

    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            acompletion_mock,
        ),
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            return_value=0.0,
        ),
    ):
        out = await adapter.execute(
            "system",
            "please write a greeting",
            tools_allowed=["file_write", "file_read", "file_list"],
        )

    assert acompletion_mock.await_count == 2
    assert out["output_ref"]["inline"] == "Done — wrote hello.txt."
    files = out["output_ref"]["files"]
    assert len(files) == 1
    assert files[0]["path"] == "hello.txt"
    assert files[0]["language"] == "text"
    assert files[0]["size"] == len("hi from the agent".encode("utf-8"))

    # File actually landed on disk under the project workspace.
    written = (tmp_path / str(project_id) / "hello.txt").read_text(encoding="utf-8")
    assert written == "hi from the agent"

    # Turn 2 messages should include the assistant's tool_calls turn and
    # the tool-role response, so OpenAI-compat servers accept the sequence.
    second_call_messages = acompletion_mock.await_args_list[1].kwargs["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles == ["system", "user", "assistant", "tool"]
    assert second_call_messages[-1]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_adapter_surfaces_tool_errors_as_payloads_not_exceptions(tmp_path, monkeypatch):
    project_id = uuid.uuid4()
    monkeypatch.setattr(
        "backend.src.core.project_workspace._root",
        lambda: tmp_path,
    )
    adapter = LiteLLMOrchestratorAdapter(model="openai/gpt-4o", project_id=project_id)

    # Model asks to read a file that doesn't exist, then replies.
    responses = [
        _fake_response(
            tool_calls=[
                {
                    "id": "call-r",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "missing.txt"}),
                    },
                }
            ],
        ),
        _fake_response(content="Could not read missing.txt; proceeding."),
    ]
    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            AsyncMock(side_effect=responses),
        ),
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            return_value=0.0,
        ) as _,
    ):
        out = await adapter.execute("sys", "work", tools_allowed=["file_read"])

    assert out["status"] == "done"
    assert out["output_ref"]["files"] == []


@pytest.mark.asyncio
async def test_adapter_does_not_send_num_ctx_for_non_ollama_model():
    adapter = LiteLLMOrchestratorAdapter(model="openai/gpt-4o", project_id=uuid.uuid4())

    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            AsyncMock(return_value=_fake_response(content="ok")),
        ) as mock_call,
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            return_value=0.0,
        ),
    ):
        await adapter.execute("sys", "hi", tools_allowed=[])

    assert "num_ctx" not in mock_call.await_args.kwargs


@pytest.mark.asyncio
async def test_adapter_swallows_cost_lookup_errors():
    adapter = LiteLLMOrchestratorAdapter(model="mystery/model", project_id=uuid.uuid4())

    def _blow_up(**_):
        raise ValueError("unknown model")

    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            AsyncMock(return_value=_fake_response(content="ok")),
        ),
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            side_effect=_blow_up,
        ),
    ):
        out = await adapter.execute("sys", "hi", tools_allowed=[])

    # Cost fell back to 0 instead of raising — keeps the run from
    # blocking just because litellm's pricing table doesn't know a
    # local model.
    assert out["actual_cost_cents"] == 0


@pytest.mark.asyncio
async def test_adapter_tools_supported_exposed_for_orchestrator_hint():
    adapter = LiteLLMOrchestratorAdapter(model="x", project_id=uuid.uuid4())
    assert "file_read" in adapter.tools_supported
    assert "file_write" in adapter.tools_supported
    assert "file_list" in adapter.tools_supported


@pytest.mark.asyncio
async def test_adapter_bails_out_when_tool_loop_exceeds_cap(tmp_path, monkeypatch):
    project_id = uuid.uuid4()
    monkeypatch.setattr(
        "backend.src.core.project_workspace._root",
        lambda: tmp_path,
    )
    monkeypatch.setattr("backend.src.core.orchestrator_adapter.MAX_TOOL_ITERATIONS", 3)
    adapter = LiteLLMOrchestratorAdapter(model="openai/gpt-4o", project_id=project_id)

    # A stubborn model that only ever calls the list tool.
    def _infinite_call():
        return _fake_response(
            tool_calls=[
                {
                    "id": "call-x",
                    "function": {
                        "name": "file_list",
                        "arguments": "{}",
                    },
                }
            ],
        )

    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            AsyncMock(side_effect=[_infinite_call(), _infinite_call(), _infinite_call()]),
        ),
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            return_value=0.0,
        ),
    ):
        out = await adapter.execute("sys", "go", tools_allowed=["file_list"])

    assert out["stop_reason"] == "tool_iterations_exhausted"
