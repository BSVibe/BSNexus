"""Tests for GatewayProvider protocol and implementations.

TDD: Written BEFORE implementation code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.src.providers.gateway import (
    BSGatewayProvider,
    ChatCompletionResult,
    GatewayProvider,
    LiteLLMDirectProvider,
)


# ---------------------------------------------------------------------------
# Protocol compliance tests
# ---------------------------------------------------------------------------
class TestGatewayProviderProtocol:
    """Verify GatewayProvider is a typing.Protocol with correct methods."""

    def test_protocol_is_runtime_checkable(self) -> None:
        """GatewayProvider should be runtime_checkable."""
        assert hasattr(GatewayProvider, "__protocol_attrs__") or hasattr(
            GatewayProvider, "__abstractmethods__"
        ), "GatewayProvider must be a Protocol"

    def test_compliant_class_is_instance(self) -> None:
        """A class with the right methods satisfies the protocol structurally."""

        class _FakeGateway:
            async def chat_completion(
                self,
                messages: list[dict[str, Any]],
                model_hint: str,
                task_metadata: dict[str, Any] | None = None,
            ) -> ChatCompletionResult:
                return ChatCompletionResult(content="test")

        assert isinstance(_FakeGateway(), GatewayProvider)

    def test_non_compliant_class_is_not_instance(self) -> None:
        """A class missing methods does NOT satisfy the protocol."""

        class _Incomplete:
            pass

        assert not isinstance(_Incomplete(), GatewayProvider)


# ---------------------------------------------------------------------------
# ChatCompletionResult tests
# ---------------------------------------------------------------------------
class TestChatCompletionResult:
    def test_creation_with_defaults(self) -> None:
        result = ChatCompletionResult(content="hello")
        assert result.content == "hello"
        assert result.model is None
        assert result.usage is None

    def test_creation_with_all_fields(self) -> None:
        usage = {"prompt_tokens": 10, "completion_tokens": 20}
        result = ChatCompletionResult(content="hi", model="gpt-4o", usage=usage)
        assert result.content == "hi"
        assert result.model == "gpt-4o"
        assert result.usage == usage


# ---------------------------------------------------------------------------
# BSGatewayProvider tests
# ---------------------------------------------------------------------------
class TestBSGatewayProvider:
    @pytest.fixture
    def provider(self) -> BSGatewayProvider:
        return BSGatewayProvider(
            base_url="https://gateway.example.com",
            api_key="test-gw-key-1234",
            timeout=30.0,
        )

    async def test_chat_completion_success(self, provider: BSGatewayProvider) -> None:
        """Should POST to BSGateway and return parsed result."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello from gateway"}}],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.src.providers.gateway.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="gpt-4o",
                task_metadata={"task_id": "t-123", "project_id": "p-456"},
            )

        assert result.content == "Hello from gateway"
        assert result.model == "gpt-4o"
        assert result.usage == {"prompt_tokens": 5, "completion_tokens": 10}

        # Verify POST was called with correct URL and headers
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://gateway.example.com/v1/chat/completions"
        headers = call_args[1].get("headers", {})
        assert headers["X-BSNexus-Task-Id"] == "t-123"
        assert headers["X-BSNexus-Project-Id"] == "p-456"

    async def test_chat_completion_with_no_metadata(self, provider: BSGatewayProvider) -> None:
        """Should work without task_metadata (no X-BSNexus headers)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "response"}}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.src.providers.gateway.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="gpt-4o",
            )

        assert result.content == "response"
        call_args = mock_client.post.call_args
        headers = call_args[1].get("headers", {})
        # No X-BSNexus headers when no metadata
        assert not any(k.startswith("X-BSNexus") for k in headers)

    async def test_chat_completion_http_error(self, provider: BSGatewayProvider) -> None:
        """Should raise on HTTP errors from BSGateway."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Internal Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("backend.src.providers.gateway.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await provider.chat_completion(
                    messages=[{"role": "user", "content": "Hi"}],
                    model_hint="gpt-4o",
                )

    async def test_api_key_sent_as_bearer(self, provider: BSGatewayProvider) -> None:
        """API key should be sent as Bearer token in Authorization header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.src.providers.gateway.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="gpt-4o",
            )

        call_args = mock_client.post.call_args
        headers = call_args[1].get("headers", {})
        assert headers["Authorization"] == "Bearer test-gw-key-1234"

    async def test_model_hint_sent_in_body(self, provider: BSGatewayProvider) -> None:
        """model_hint should be sent in the request body."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.src.providers.gateway.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="claude-sonnet",
            )

        call_args = mock_client.post.call_args
        body = call_args[1].get("json", {})
        assert body["model"] == "claude-sonnet"
        assert body["messages"] == [{"role": "user", "content": "Hi"}]


# ---------------------------------------------------------------------------
# LiteLLMDirectProvider tests
# ---------------------------------------------------------------------------
class TestLiteLLMDirectProvider:
    @pytest.fixture
    def provider(self) -> LiteLLMDirectProvider:
        return LiteLLMDirectProvider(
            api_key="sk-test-key-5678",
            default_model="gpt-4o",
        )

    async def test_chat_completion_success(self, provider: LiteLLMDirectProvider) -> None:
        """Should call litellm.acompletion directly and return result."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Direct LLM response"
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.usage.total_tokens = 30

        with patch("backend.src.providers.gateway.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            result = await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="gpt-4o",
            )

        assert result.content == "Direct LLM response"
        assert result.model == "gpt-4o"
        mock_acompletion.assert_called_once()

    async def test_chat_completion_uses_model_hint(self, provider: LiteLLMDirectProvider) -> None:
        """model_hint should be passed to litellm."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_response.choices = [mock_choice]
        mock_response.model = "claude-sonnet"
        mock_response.usage = None

        with patch("backend.src.providers.gateway.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="claude-sonnet",
            )

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet"

    async def test_chat_completion_uses_default_model_when_hint_empty(self, provider: LiteLLMDirectProvider) -> None:
        """Should fall back to default_model when model_hint is empty."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o"
        mock_response.usage = None

        with patch("backend.src.providers.gateway.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="",
            )

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["model"] == "gpt-4o"

    async def test_chat_completion_passes_api_key(self, provider: LiteLLMDirectProvider) -> None:
        """Should pass api_key to litellm.acompletion."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o"
        mock_response.usage = None

        with patch("backend.src.providers.gateway.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="gpt-4o",
            )

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["api_key"] == "sk-test-key-5678"

    async def test_chat_completion_litellm_error(self, provider: LiteLLMDirectProvider) -> None:
        """Should propagate litellm errors."""
        with patch("backend.src.providers.gateway.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = Exception("API error")

            with pytest.raises(Exception, match="API error"):
                await provider.chat_completion(
                    messages=[{"role": "user", "content": "Hi"}],
                    model_hint="gpt-4o",
                )

    async def test_chat_completion_task_metadata_ignored(self, provider: LiteLLMDirectProvider) -> None:
        """task_metadata should be accepted but not affect the call (direct provider has no gateway)."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o"
        mock_response.usage = None

        with patch("backend.src.providers.gateway.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            result = await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="gpt-4o",
                task_metadata={"task_id": "t-123"},
            )

        assert result.content == "response"

    async def test_chat_completion_with_base_url(self) -> None:
        """Should pass base_url to litellm when configured."""
        provider = LiteLLMDirectProvider(
            api_key="sk-test",
            default_model="gpt-4o",
            base_url="https://custom-llm.example.com",
        )

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o"
        mock_response.usage = None

        with patch("backend.src.providers.gateway.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            await provider.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                model_hint="gpt-4o",
            )

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["api_base"] == "https://custom-llm.example.com"
