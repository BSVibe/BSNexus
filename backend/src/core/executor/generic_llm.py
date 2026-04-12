"""Generic LLM executor — agentic loop with tool_use support via LiteLLM.

This is the primary executor for all non-worker, non-CLI agents.
Supports the full tool_use cycle: LLM → tool_call → execute → repeat.
Provider-agnostic via litellm (OpenAI, Anthropic, etc.).

Replaces the old single-shot claude_api, bsgateway, and codex executors.
"""

# Re-export everything from litellm_executor for backward compatibility.
# The canonical implementation lives in litellm_executor.py.
from backend.src.core.executor.litellm_executor import (  # noqa: F401
    ExecutionEvent,
    ExecutionResult,
    LiteLLMExecutor,
    TokenUsage,
)

# Alias: the registry and templates use "generic_llm" as the executor name.
GenericLLMExecutor = LiteLLMExecutor

__all__ = [
    "ExecutionEvent",
    "ExecutionResult",
    "GenericLLMExecutor",
    "LiteLLMExecutor",
    "TokenUsage",
]
