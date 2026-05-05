"""Stage 4-light — live ``DirectLLMAdapter`` smoke against the
local ollama daemon.

Skipped by default (no marker selected). Opt in with::

    cd backend
    uv run --project . pytest -m live_ollama --no-header

Pre-requisites:
- ``ollama serve`` reachable on ``http://localhost:11434``
- ``ollama pull qwen3-coder:30b`` (or override via ``OLLAMA_TEST_MODEL``)

What this proves:
- Our :class:`DirectLLMAdapter` (``executor_type=llm_api`` path) wires
  end-to-end through litellm → ollama → real local model and returns
  the documented executor-result shape (``output_ref``,
  ``finish_reason``, ``actual_cost_cents``).
- No MCP, no tool dispatch — orchestrator-side behaviour is covered by
  the mocked unit tests in ``test_direct_llm_adapter.py``.
"""

from __future__ import annotations

import os
import socket
import uuid

import pytest

from backend.src.core.llm.direct_client import DirectLLMAdapter

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1")
OLLAMA_PORT = int(os.getenv("OLLAMA_PORT", "11434"))
OLLAMA_MODEL = os.getenv("OLLAMA_TEST_MODEL", "ollama/qwen3-coder:30b")


def _ollama_reachable() -> bool:
    try:
        with socket.create_connection((OLLAMA_HOST, OLLAMA_PORT), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.live_ollama
@pytest.mark.timeout(180)
@pytest.mark.skipif(
    not _ollama_reachable(),
    reason=f"ollama daemon not reachable at {OLLAMA_HOST}:{OLLAMA_PORT}",
)
async def test_direct_llm_adapter_returns_local_completion() -> None:
    """Round-trip a small coding prompt through the adapter."""
    chunks: list[str] = []

    async def collect(chunk: str) -> None:
        chunks.append(chunk)

    adapter = DirectLLMAdapter(
        model=OLLAMA_MODEL,
        api_key="any-non-empty",  # litellm requires non-empty for ollama
        project_id=uuid.uuid4(),
        workspace_dir=None,
        mcp_servers=None,
        on_chunk=collect,
        # Exercise the ``api_base`` plumbing path the same way
        # production hits ollama via a Tailscale IP — we just point at
        # the same loopback the daemon listens on.
        api_base=f"http://{OLLAMA_HOST}:{OLLAMA_PORT}",
    )

    result = await adapter.execute(
        system_prompt=(
            "You are a terse coding assistant. Reply only with the requested code. No commentary, no markdown fences."
        ),
        user_prompt=(
            "Write a Python function `is_palindrome(s: str) -> bool` that returns "
            "True iff `s` reads the same forward and backward, ignoring case "
            "and non-alphanumeric characters."
        ),
        tools_allowed=[],
    )

    assert result.get("finish_reason") == "stop", result
    assert result.get("output_type") == "text", result
    assert result.get("actual_cost_cents") == 0, result
    assert result.get("output_ref"), "ollama returned empty output"
    assert "".join(chunks), "no streaming chunks observed"
    # Loose semantic check — qwen3-coder reliably emits a `def` keyword
    # for "Write a Python function" prompts. Don't assert function
    # signature / name to avoid false negatives if the model paraphrases.
    assert "def " in result["output_ref"], result["output_ref"]
