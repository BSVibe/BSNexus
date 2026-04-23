"""LiteLLMOrchestratorAdapter — bridges orchestrator → litellm.acompletion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.src.core.orchestrator_adapter import LiteLLMOrchestratorAdapter


def _fake_response(content: str, *, prompt_tokens: int = 0, completion_tokens: int = 0):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


@pytest.mark.asyncio
async def test_adapter_returns_orchestrator_shaped_dict():
    adapter = LiteLLMOrchestratorAdapter(
        model="ollama/glm-4.7-flash:latest",
        api_key="unused",
        base_url="http://localhost:11434",
    )

    with patch(
        "backend.src.core.orchestrator_adapter.litellm.acompletion",
        AsyncMock(return_value=_fake_response("hello world", prompt_tokens=40, completion_tokens=12)),
    ) as mock_call, patch(
        "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
        return_value=(0.001, 0.001),
    ):
        out = await adapter.execute(
            "You are a helpful assistant. Task: greet.",
            tools_allowed=["read"],
        )

    mock_call.assert_awaited_once()
    kwargs = mock_call.await_args.kwargs
    assert kwargs["messages"] == [
        {"role": "user", "content": "You are a helpful assistant. Task: greet."}
    ]
    assert kwargs["model"] == "ollama/glm-4.7-flash:latest"
    assert kwargs["api_base"] == "http://localhost:11434"
    # Ollama models get a larger num_ctx hint so local backends don't truncate.
    assert "num_ctx" in kwargs

    assert out["status"] == "done"
    assert out["output_type"] == "text"
    assert out["output_ref"] == {"inline": "hello world"}
    assert out["prompt_tokens"] == 40
    assert out["completion_tokens"] == 12
    assert out["stop_reason"] == "stop"
    # (0.001 + 0.001) USD × 100 = 0.2 cents → rounds to 0.
    assert out["actual_cost_cents"] == 0


@pytest.mark.asyncio
async def test_adapter_does_not_send_num_ctx_for_non_ollama_model():
    adapter = LiteLLMOrchestratorAdapter(model="openai/gpt-4o")

    with patch(
        "backend.src.core.orchestrator_adapter.litellm.acompletion",
        AsyncMock(return_value=_fake_response("ok")),
    ) as mock_call, patch(
        "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
        return_value=0.0,
    ):
        await adapter.execute("hi", tools_allowed=[])

    assert "num_ctx" not in mock_call.await_args.kwargs


@pytest.mark.asyncio
async def test_adapter_swallows_cost_lookup_errors():
    adapter = LiteLLMOrchestratorAdapter(model="mystery/model")

    def _blow_up(**_):
        raise ValueError("unknown model")

    with patch(
        "backend.src.core.orchestrator_adapter.litellm.acompletion",
        AsyncMock(return_value=_fake_response("ok")),
    ), patch(
        "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
        side_effect=_blow_up,
    ):
        out = await adapter.execute("hi", tools_allowed=[])

    # Cost fell back to 0 instead of raising — keeps the run from
    # blocking just because litellm's pricing table doesn't know a
    # local model.
    assert out["actual_cost_cents"] == 0


@pytest.mark.asyncio
async def test_adapter_tools_supported_exposed_for_orchestrator_hint():
    adapter = LiteLLMOrchestratorAdapter(model="x")
    assert "read" in adapter.tools_supported
    assert "write" in adapter.tools_supported
