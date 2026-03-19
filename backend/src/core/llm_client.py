from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator, Optional, cast

import litellm
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import Choices, ModelResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds
RETRY_MAX_DELAY = 10.0  # seconds
REQUEST_TIMEOUT = 120  # seconds — per acompletion call (connect + read)

_RETRYABLE_STRINGS = ("overloaded", "rate_limit", "timeout", "429", "503", "529")


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    msg = str(exc).lower()
    return any(s in msg for s in _RETRYABLE_STRINGS)


def _extract_json(raw: str) -> dict[str, Any]:
    """Parse JSON from LLM response, handling markdown code blocks."""
    text = raw.strip()
    if not text:
        raise json.JSONDecodeError("Empty response from LLM", text, 0)
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code block
    match = _JSON_BLOCK_RE.search(text)
    if match:
        return json.loads(match.group(1))
    raise json.JSONDecodeError("No valid JSON found in LLM response", text, 0)


class LLMError(Exception):
    """Custom exception wrapping LiteLLM errors."""

    def __init__(self, message: str, original_error: Exception | None = None, *, retryable: bool = False) -> None:
        self.original_error = original_error
        self.retryable = retryable
        super().__init__(message)


class LLMConfig(BaseModel):
    """LLM connection config (passed at runtime)."""

    api_key: str
    model: str = "anthropic/claude-sonnet-4-20250514"
    base_url: Optional[str] = None

    def __repr__(self) -> str:
        masked_key = f"***{self.api_key[-4:]}" if len(self.api_key) >= 4 else "***"
        return f"LLMConfig(api_key='{masked_key}', model='{self.model}', base_url={self.base_url!r})"


class LLMClient:
    """LiteLLM-based LLM client (provider-agnostic)."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Non-streaming response with automatic retry on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = cast(
                    ModelResponse,
                    await litellm.acompletion(
                        model=self.config.model,
                        messages=messages,
                        api_key=self.config.api_key,
                        api_base=self.config.base_url,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=REQUEST_TIMEOUT,
                    ),
                )
                choice = cast(Choices, response.choices[0])
                content = choice.message.content
                if content is None:
                    raise LLMError("LLM returned empty content")
                return content
            except Exception as e:
                last_exc = e
                if attempt < MAX_RETRIES and _is_retryable(e):
                    delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
                    logger.warning(
                        "LLM chat attempt %d failed (retryable): %s. Retrying in %.1fs", attempt + 1, e, delay
                    )
                    await asyncio.sleep(delay)
                    continue
                raise LLMError(
                    f"LLM chat failed: {e}",
                    original_error=e,
                    retryable=_is_retryable(e),
                ) from e
        # Should not reach here, but satisfy type checker
        raise LLMError(f"LLM chat failed after {MAX_RETRIES + 1} attempts: {last_exc}", original_error=last_exc)

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Streaming response with automatic retry on transient errors.

        Retries only when the error occurs before any chunks have been yielded.
        Once streaming has started, errors are raised immediately since partial
        content has already been sent to the client.
        """
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            started_streaming = False
            try:
                stream = cast(
                    CustomStreamWrapper,
                    await litellm.acompletion(
                        model=self.config.model,
                        messages=messages,
                        api_key=self.config.api_key,
                        api_base=self.config.base_url,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=True,
                        timeout=REQUEST_TIMEOUT,
                    ),
                )
                async for chunk in stream:
                    content = chunk.choices[0].delta.content
                    if content:
                        started_streaming = True
                        yield content
                return  # Success, exit retry loop
            except LLMError:
                raise
            except Exception as e:
                last_exc = e
                if started_streaming:
                    # Already sent chunks to client, can't retry transparently
                    raise LLMError(
                        f"LLM stream_chat failed mid-stream: {e}",
                        original_error=e,
                        retryable=_is_retryable(e),
                    ) from e
                if attempt < MAX_RETRIES and _is_retryable(e):
                    delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
                    logger.warning(
                        "LLM stream_chat attempt %d failed (retryable): %s. Retrying in %.1fs",
                        attempt + 1,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise LLMError(
                    f"LLM stream_chat failed: {e}",
                    original_error=e,
                    retryable=_is_retryable(e),
                ) from e
        raise LLMError(
            f"LLM stream_chat failed after {MAX_RETRIES + 1} attempts: {last_exc}",
            original_error=last_exc,
        )

    async def structured_output(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any],
        temperature: float = 0.3,
        max_tokens: int = 16384,
    ) -> dict[str, Any]:
        """JSON structured output via streaming with automatic retry on transient errors.

        Uses streaming to avoid connection timeouts on long-running requests.
        Retries only when the error occurs before any chunks have been received.
        """
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            started_streaming = False
            try:
                logger.info(
                    "structured_output: model=%s, messages=%d, attempt=%d",
                    self.config.model,
                    len(messages),
                    attempt + 1,
                )
                stream = cast(
                    CustomStreamWrapper,
                    await litellm.acompletion(
                        model=self.config.model,
                        messages=messages,
                        api_key=self.config.api_key,
                        api_base=self.config.base_url,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format=response_format,
                        stream=True,
                        timeout=REQUEST_TIMEOUT,
                    ),
                )
                raw = ""
                async for chunk in stream:
                    content = chunk.choices[0].delta.content
                    if content:
                        started_streaming = True
                        raw += content
                return _extract_json(raw)
            except json.JSONDecodeError as e:
                raise LLMError(f"Failed to parse structured output as JSON: {e}", original_error=e) from e
            except LLMError:
                raise
            except Exception as e:
                last_exc = e
                if started_streaming:
                    raise LLMError(
                        f"LLM structured_output failed mid-stream: {e}",
                        original_error=e,
                        retryable=_is_retryable(e),
                    ) from e
                if attempt < MAX_RETRIES and _is_retryable(e):
                    delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
                    logger.warning(
                        "structured_output attempt %d failed (retryable): %s. Retrying in %.1fs",
                        attempt + 1,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.exception("structured_output failed: %s", e)
                raise LLMError(
                    f"LLM structured_output failed: {e}",
                    original_error=e,
                    retryable=_is_retryable(e),
                ) from e
        raise LLMError(
            f"LLM structured_output failed after {MAX_RETRIES + 1} attempts: {last_exc}",
            original_error=last_exc,
        )


def create_llm_client(config: LLMConfig) -> LLMClient:
    """Create client from LLMConfig."""
    return LLMClient(config)


def create_llm_client_from_project(project: Any, role: str = "architect") -> LLMClient:
    """Create client from Project model's llm_config.

    Args:
        project: Project SQLAlchemy model.
        role: "architect" or "pm".

    Returns:
        LLMClient configured for the given role.

    Raises:
        ValueError: if llm_config doesn't have config for the given role.
    """
    llm_config: dict[str, Any] = project.llm_config or {}
    role_config: dict[str, Any] | None = llm_config.get(role)

    if not role_config or not role_config.get("api_key"):
        raise ValueError(f"Project {project.id} has no {role} LLM configuration")

    config = LLMConfig(
        api_key=role_config["api_key"],
        model=role_config.get("model", "anthropic/claude-sonnet-4-20250514"),
        base_url=role_config.get("base_url"),
    )
    return LLMClient(config)
