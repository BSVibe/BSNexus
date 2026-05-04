"""Direct-LLM path for ``executor_type=generic_llm``.

Phase 2b of the BSVibe-optional restructure (2026-05-04). Surface:

- :class:`DirectLLMAdapter` — orchestrator-facing executor that
  satisfies the same ``execute(system_prompt, user_prompt, *,
  tools_allowed, history)`` contract as
  :class:`backend.src.core.bsgateway.BSGatewayAdapter`. Internally
  drives a litellm completion + MCP tool loop so the model has
  callbacks into BSNexus's MCP server (``decision.create``,
  ``artifact.read``, etc.) without going through BSGateway.

Provider-agnostic — claude / gpt / gemini / ollama-local all hit the
same code path because litellm normalises the provider boundary. The
MCP transport is streamable-HTTP (BSNexus's ``/mcp/http`` endpoint).
"""

from backend.src.core.llm.direct_client import DirectLLMAdapter, DirectLLMError

__all__ = ["DirectLLMAdapter", "DirectLLMError"]
