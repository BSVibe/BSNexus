"""Tests for LiteLLM executor timeout + retry logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.executor.litellm_executor import (
    LLM_RETRY_ON_TIMEOUT,
    REQUEST_TIMEOUT,
    LiteLLMExecutor,
)


def _make_success_stream(content: str = "Hello"):
    """Build a mock streaming response for successful completion."""
    from backend.tests.test_tools.test_litellm_executor import _MockStream, _MockStreamChunk
    return _MockStream([
        _MockStreamChunk(content=content),
        _MockStreamChunk(
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
    ])


class TestExecutorTimeoutConfig:
    """Verify timeout constants are set correctly."""

    def test_request_timeout_is_generous_for_local_models(self) -> None:
        assert REQUEST_TIMEOUT >= 300

    def test_retry_on_timeout_is_1(self) -> None:
        assert LLM_RETRY_ON_TIMEOUT == 1


class TestExecutorRetryOnTimeout:
    """LLM call should retry once on transient errors (timeout, connection refused)."""

    @pytest.mark.asyncio
    async def test_retries_on_timeout_error(self) -> None:
        executor = LiteLLMExecutor()

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                Exception("Connection timeout after 180 seconds"),
                _make_success_stream("Hello"),
            ]

            result = await executor.execute(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                tool_handler=None,
                model="test-model",
                api_key="test-key",
            )

            assert result.content == "Hello"
            assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_connection_refused(self) -> None:
        executor = LiteLLMExecutor()

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                Exception("Connection refused"),
                _make_success_stream("OK"),
            ]

            result = await executor.execute(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                tool_handler=None,
                model="test-model",
                api_key="test-key",
            )

            assert result.content == "OK"
            assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_non_transient_error(self) -> None:
        executor = LiteLLMExecutor()

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("Invalid API key")

            with pytest.raises(Exception, match="Invalid API key"):
                await executor.execute(
                    messages=[{"role": "user", "content": "hi"}],
                    tools=None,
                    tool_handler=None,
                    model="test-model",
                    api_key="bad-key",
                )

            assert mock_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self) -> None:
        executor = LiteLLMExecutor()

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("timeout waiting for response")

            with pytest.raises(Exception, match="timeout"):
                await executor.execute(
                    messages=[{"role": "user", "content": "hi"}],
                    tools=None,
                    tool_handler=None,
                    model="test-model",
                    api_key="test-key",
                )

            assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_success_without_retry(self) -> None:
        executor = LiteLLMExecutor()

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _make_success_stream("Direct success")

            result = await executor.execute(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                tool_handler=None,
                model="test-model",
                api_key="test-key",
            )

            assert result.content == "Direct success"
            assert mock_llm.call_count == 1
