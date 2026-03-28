"""GatewayProvider protocol and implementations.

Self-contained module — no cross-provider imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
import litellm
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ChatCompletionResult:
    """Result from a chat completion call."""

    content: str
    model: str | None = None
    usage: dict[str, Any] | None = None


@runtime_checkable
class GatewayProvider(Protocol):
    """LLM gateway interface using structural subtyping."""

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model_hint: str,
        task_metadata: dict[str, Any] | None = None,
    ) -> ChatCompletionResult: ...


class BSGatewayProvider:
    """Routes LLM requests through BSGateway API with task metadata headers."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model_hint: str,
        task_metadata: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        """Send chat completion request to BSGateway with X-BSNexus-* headers."""
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        if task_metadata:
            for key, value in task_metadata.items():
                header_name = f"X-BSNexus-{key.replace('_', '-').title()}"
                headers[header_name] = str(value)

        body: dict[str, Any] = {
            "model": model_hint,
            "messages": messages,
        }

        logger.info("bsgateway_request", model=model_hint, task_metadata_keys=list(task_metadata or {}))

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        return ChatCompletionResult(
            content=content,
            model=data.get("model"),
            usage=data.get("usage"),
        )


class LiteLLMDirectProvider:
    """Direct litellm.acompletion fallback — no external gateway needed."""

    def __init__(
        self,
        api_key: str = "",
        default_model: str = "anthropic/claude-sonnet-4-20250514",
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._base_url = base_url
        self._timeout = timeout

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model_hint: str,
        task_metadata: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        """Call litellm.acompletion directly."""
        model = model_hint or self._default_model

        logger.info("litellm_direct_request", model=model)

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            api_key=self._api_key,
            api_base=self._base_url,
            timeout=self._timeout,
        )

        choice = response.choices[0]
        content = choice.message.content

        usage_data: dict[str, Any] | None = None
        if response.usage:
            usage_data = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ChatCompletionResult(
            content=content,
            model=getattr(response, "model", None),
            usage=usage_data,
        )
