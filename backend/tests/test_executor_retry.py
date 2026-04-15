"""Tests for LiteLLM executor timeout + retry logic."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.executor.litellm_executor import (
    LLM_RETRY_ON_TIMEOUT,
    REQUEST_TIMEOUT,
    ExecutionResult,
    LiteLLMExecutor,
)
from backend.src.tools.base import ToolDefinition


class TestExecutorTimeoutConfig:
    """Verify timeout constants are set correctly."""

    def test_request_timeout_is_180(self) -> None:
        assert REQUEST_TIMEOUT == 180

    def test_retry_on_timeout_is_1(self) -> None:
        assert LLM_RETRY_ON_TIMEOUT == 1


class TestExecutorRetryOnTimeout:
    """LLM call should retry once on transient errors (timeout, connection refused)."""

    @pytest.mark.asyncio
    async def test_retries_on_timeout_error(self) -> None:
        executor = LiteLLMExecutor()

        # First call: timeout error. Second call: success.
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "Hello"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                Exception("Connection timeout after 180 seconds"),
                mock_response,
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

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "OK"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                Exception("Connection refused"),
                mock_response,
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

            # Should NOT retry on non-transient errors
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

            # 1 initial + 1 retry = 2
            assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_success_without_retry(self) -> None:
        executor = LiteLLMExecutor()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "Direct success"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response

            result = await executor.execute(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                tool_handler=None,
                model="test-model",
                api_key="test-key",
            )

            assert result.content == "Direct success"
            assert mock_llm.call_count == 1
