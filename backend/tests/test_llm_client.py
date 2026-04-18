from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.config import settings
from backend.src.core.llm_client import (
    REQUEST_TIMEOUT,
    LLMClient,
    LLMConfig,
    LLMError,
    LLMResponse,
    _is_retryable,
    create_llm_client,
    create_llm_client_from_project,
)


# -- LLMConfig ----------------------------------------------------------------


class TestLLMConfig:
    def test_creation_requires_model(self) -> None:
        """LLMConfig requires both api_key and model."""
        config = LLMConfig(api_key="sk-test-key-1234", model="anthropic/claude-sonnet-4-20250514")
        assert config.api_key == "sk-test-key-1234"
        assert config.model == "anthropic/claude-sonnet-4-20250514"
        assert config.base_url is None

    def test_creation_with_all_fields(self) -> None:
        """LLMConfig should accept all fields."""
        config = LLMConfig(api_key="sk-abc123", model="gpt-4o", base_url="https://custom.api.com")
        assert config.api_key == "sk-abc123"
        assert config.model == "gpt-4o"
        assert config.base_url == "https://custom.api.com"

    def test_repr_masks_api_key(self) -> None:
        """__repr__ should mask the API key, showing only last 4 chars."""
        config = LLMConfig(api_key="sk-very-secret-key-abcd", model="gpt-4o")
        repr_str = repr(config)
        assert "sk-very-secret-key-abcd" not in repr_str
        assert "***abcd" in repr_str
        assert "model=" in repr_str

    def test_repr_masks_short_api_key(self) -> None:
        """__repr__ should handle short API keys gracefully."""
        config = LLMConfig(api_key="abc", model="gpt-4o")
        repr_str = repr(config)
        assert "abc" not in repr_str
        assert "***" in repr_str

    def test_repr_includes_base_url(self) -> None:
        """__repr__ should include base_url when set."""
        config = LLMConfig(api_key="sk-test1234", model="gpt-4o", base_url="https://api.example.com")
        repr_str = repr(config)
        assert "https://api.example.com" in repr_str


# -- LLMResponse --------------------------------------------------------------


class TestLLMResponse:
    def test_cost_cents_rounds_up(self) -> None:
        resp = LLMResponse(content="hi", cost_usd=0.0015)
        assert resp.cost_cents == 1  # ceil(0.15) = 1

    def test_cost_cents_zero(self) -> None:
        resp = LLMResponse(content="hi", cost_usd=0.0)
        assert resp.cost_cents == 0

    def test_cost_cents_exact(self) -> None:
        resp = LLMResponse(content="hi", cost_usd=0.05)
        assert resp.cost_cents == 5


# -- LLMClient.chat -----------------------------------------------------------


class TestLLMClientChat:
    @pytest.fixture
    def config(self) -> LLMConfig:
        return LLMConfig(api_key="sk-test-key-1234", model="gpt-4o")

    @pytest.fixture
    def client(self, config: LLMConfig) -> LLMClient:
        return LLMClient(config)

    async def test_chat_returns_content(self, client: LLMClient) -> None:
        """chat() should return LLMResponse with content and usage from litellm response."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello, world!"
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_response.model = "gpt-4o"

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion, \
             patch("backend.src.core.llm_client.litellm.completion_cost", return_value=0.0015):
            mock_acompletion.return_value = mock_response
            result = await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello, world!"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5
        assert result.total_tokens == 15
        assert result.model == "gpt-4o"
        mock_acompletion.assert_called_once_with(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hi"}],
            api_key="sk-test-key-1234",
            api_base=None,
            temperature=0.7,
            max_tokens=4096,
            timeout=REQUEST_TIMEOUT,
        )

    async def test_chat_custom_params(self, client: LLMClient) -> None:
        """chat() should pass custom temperature and max_tokens."""
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Response"
        mock_response.choices = [mock_choice]

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
                temperature=0.2,
                max_tokens=1024,
            )

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["temperature"] == 0.2
        assert call_kwargs["max_tokens"] == 1024

    async def test_chat_raises_llm_error_on_exception(self, client: LLMClient) -> None:
        """chat() should wrap exceptions in LLMError."""
        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = Exception("API key invalid")

            with pytest.raises(LLMError, match="LLM chat failed"):
                await client.chat(messages=[{"role": "user", "content": "Hi"}])

    async def test_chat_llm_error_preserves_original(self, client: LLMClient) -> None:
        """LLMError should preserve the original exception."""
        original = ValueError("rate limited")
        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = original

            with pytest.raises(LLMError) as exc_info:
                await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert exc_info.value.original_error is original


# -- LLMClient.stream_chat ----------------------------------------------------


class TestLLMClientStreamChat:
    @pytest.fixture
    def config(self) -> LLMConfig:
        return LLMConfig(api_key="sk-stream-key-5678", model="gpt-4o")

    @pytest.fixture
    def client(self, config: LLMConfig) -> LLMClient:
        return LLMClient(config)

    async def test_stream_chat_yields_content(self, client: LLMClient) -> None:
        """stream_chat() should yield content chunks from streaming response."""
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Hello"

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = " world"

        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta.content = None  # End of stream delta

        async def mock_stream():
            for chunk in [chunk1, chunk2, chunk3]:
                yield chunk

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_stream()

            chunks: list[str] = []
            async for content in client.stream_chat(messages=[{"role": "user", "content": "Hi"}]):
                chunks.append(content)

        assert chunks == ["Hello", " world"]
        mock_acompletion.assert_called_once_with(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hi"}],
            api_key="sk-stream-key-5678",
            api_base=None,
            temperature=0.7,
            max_tokens=4096,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )

    async def test_stream_chat_skips_none_content(self, client: LLMClient) -> None:
        """stream_chat() should skip chunks where content is None."""
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = None

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = "data"

        async def mock_stream():
            for chunk in [chunk1, chunk2]:
                yield chunk

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_stream()

            chunks: list[str] = []
            async for content in client.stream_chat(messages=[{"role": "user", "content": "Test"}]):
                chunks.append(content)

        assert chunks == ["data"]

    async def test_stream_chat_raises_llm_error_on_acompletion_failure(self, client: LLMClient) -> None:
        """stream_chat() should wrap acompletion exceptions in LLMError."""
        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = Exception("Connection refused")

            with pytest.raises(LLMError, match="LLM stream_chat failed"):
                async for _ in client.stream_chat(messages=[{"role": "user", "content": "Hi"}]):
                    pass

    async def test_stream_chat_raises_llm_error_on_iteration_failure(self, client: LLMClient) -> None:
        """stream_chat() should wrap iteration exceptions in LLMError."""

        async def mock_stream():
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content="ok"))])
            raise RuntimeError("Stream broken")

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_stream()

            with pytest.raises(LLMError, match="LLM stream_chat failed"):
                async for _ in client.stream_chat(messages=[{"role": "user", "content": "Hi"}]):
                    pass


# -- LLMClient.structured_output ----------------------------------------------


class TestLLMClientStructuredOutput:
    @pytest.fixture
    def config(self) -> LLMConfig:
        return LLMConfig(api_key="sk-struct-key-9999", model="gpt-4o")

    @pytest.fixture
    def client(self, config: LLMConfig) -> LLMClient:
        return LLMClient(config)

    @staticmethod
    def _make_stream(text: str):
        """Create an async generator that yields streaming chunks for the given text."""

        async def stream():
            for char in text:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = char
                yield chunk

        return stream()

    async def test_structured_output_returns_parsed_json(self, client: LLMClient) -> None:
        """structured_output() should parse JSON from the streamed response."""
        json_text = '{"tasks": [{"title": "Task 1"}], "count": 1}'
        response_format = {"type": "json_object"}

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = self._make_stream(json_text)
            result = await client.structured_output(
                messages=[{"role": "user", "content": "Decompose"}],
                response_format=response_format,
            )

        assert result == {"tasks": [{"title": "Task 1"}], "count": 1}
        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["temperature"] == 0.3
        assert call_kwargs["max_tokens"] == 16384
        assert call_kwargs["response_format"] == response_format
        assert call_kwargs["stream"] is True

    async def test_structured_output_raises_on_invalid_json(self, client: LLMClient) -> None:
        """structured_output() should raise LLMError when response is not valid JSON."""
        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = self._make_stream("not valid json {{")

            with pytest.raises(LLMError, match="Failed to parse structured output as JSON"):
                await client.structured_output(
                    messages=[{"role": "user", "content": "Test"}],
                    response_format={"type": "json_object"},
                )

    async def test_structured_output_raises_llm_error_on_exception(self, client: LLMClient) -> None:
        """structured_output() should wrap litellm exceptions in LLMError."""
        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = Exception("Model not found")

            with pytest.raises(LLMError, match="LLM structured_output failed"):
                await client.structured_output(
                    messages=[{"role": "user", "content": "Test"}],
                    response_format={"type": "json_object"},
                )

    async def test_structured_output_raises_on_mid_stream_error(self, client: LLMClient) -> None:
        """structured_output() should raise LLMError without retry on mid-stream errors."""

        async def broken_stream():
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = '{"partial'
            yield chunk
            raise RuntimeError("Stream broken")

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = broken_stream()

            with pytest.raises(LLMError, match="mid-stream"):
                await client.structured_output(
                    messages=[{"role": "user", "content": "Test"}],
                    response_format={"type": "json_object"},
                )

        assert mock_acompletion.call_count == 1


# -- create_llm_client ---------------------------------------------------------


class TestCreateLLMClient:
    def test_creates_client_from_config(self) -> None:
        """create_llm_client should return an LLMClient with the given config."""
        config = LLMConfig(api_key="sk-factory-1234", model="gpt-4o-mini")
        client = create_llm_client(config)

        assert isinstance(client, LLMClient)
        assert client.config is config
        assert client.config.api_key == "sk-factory-1234"
        assert client.config.model == "gpt-4o-mini"


# -- create_llm_client_from_project --------------------------------------------


class TestCreateLLMClientFromProject:
    def _make_project(self, llm_config: dict | None = None) -> MagicMock:
        """Create a mock Project with the given llm_config."""
        project = MagicMock()
        project.id = uuid.uuid4()
        project.llm_config = llm_config
        return project

    def test_creates_client_for_architect_role(self) -> None:
        """create_llm_client_from_project should create a client for the 'architect' role."""
        project = self._make_project(
            llm_config={
                "architect": {
                    "api_key": "sk-arch-key",
                    "model": "claude-3-opus",
                    "base_url": "https://arch.api.com",
                },
            }
        )

        client = create_llm_client_from_project(project, role="architect")

        assert isinstance(client, LLMClient)
        assert client.config.api_key == "sk-arch-key"
        assert client.config.model == "claude-3-opus"
        assert client.config.base_url == "https://arch.api.com"

    def test_creates_client_for_pm_role(self) -> None:
        """create_llm_client_from_project should create a client for the 'pm' role."""
        project = self._make_project(
            llm_config={
                "pm": {
                    "api_key": "sk-pm-key",
                    "model": "gpt-4o",
                },
            }
        )

        client = create_llm_client_from_project(project, role="pm")

        assert isinstance(client, LLMClient)
        assert client.config.api_key == "sk-pm-key"
        assert client.config.model == "gpt-4o"

    def test_uses_empty_model_when_not_specified(self) -> None:
        """create_llm_client_from_project should use empty model when role config has no model."""
        project = self._make_project(
            llm_config={
                "architect": {
                    "api_key": "sk-arch-key",
                },
            }
        )

        client = create_llm_client_from_project(project, role="architect")

        assert client.config.model == ""

    def test_defaults_to_architect_role(self) -> None:
        """create_llm_client_from_project should default to 'architect' role."""
        project = self._make_project(
            llm_config={
                "architect": {
                    "api_key": "sk-arch-default",
                },
            }
        )

        client = create_llm_client_from_project(project)

        assert client.config.api_key == "sk-arch-default"

    def test_raises_when_llm_config_is_none(self) -> None:
        """create_llm_client_from_project should raise ValueError when llm_config is None."""
        project = self._make_project(llm_config=None)

        with pytest.raises(ValueError, match="has no architect LLM configuration"):
            create_llm_client_from_project(project, role="architect")

    def test_raises_when_role_config_missing(self) -> None:
        """create_llm_client_from_project should raise ValueError when role config is missing."""
        project = self._make_project(
            llm_config={
                "architect": {"api_key": "sk-arch-key"},
            }
        )

        with pytest.raises(ValueError, match="has no pm LLM configuration"):
            create_llm_client_from_project(project, role="pm")

    def test_raises_when_api_key_missing(self) -> None:
        """create_llm_client_from_project should raise ValueError when api_key is missing."""
        project = self._make_project(
            llm_config={
                "architect": {"model": "gpt-4o"},  # no api_key
            }
        )

        with pytest.raises(ValueError, match="has no architect LLM configuration"):
            create_llm_client_from_project(project, role="architect")

    def test_raises_when_api_key_is_empty(self) -> None:
        """create_llm_client_from_project should raise ValueError when api_key is empty string."""
        project = self._make_project(
            llm_config={
                "architect": {"api_key": "", "model": "gpt-4o"},
            }
        )

        with pytest.raises(ValueError, match="has no architect LLM configuration"):
            create_llm_client_from_project(project, role="architect")


# -- LLMError ------------------------------------------------------------------


class TestLLMError:
    def test_llm_error_message(self) -> None:
        """LLMError should store the message."""
        error = LLMError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.original_error is None

    def test_llm_error_with_original(self) -> None:
        """LLMError should preserve the original exception."""
        original = RuntimeError("original cause")
        error = LLMError("Wrapped error", original_error=original)
        assert str(error) == "Wrapped error"
        assert error.original_error is original

    def test_llm_error_is_exception(self) -> None:
        """LLMError should be a subclass of Exception."""
        assert issubclass(LLMError, Exception)

    def test_llm_error_retryable_flag(self) -> None:
        """LLMError should support retryable flag."""
        error = LLMError("Overloaded", retryable=True)
        assert error.retryable is True

        error2 = LLMError("Bad request", retryable=False)
        assert error2.retryable is False

        error3 = LLMError("Default")
        assert error3.retryable is False


# -- _is_retryable ---------------------------------------------------------------


class TestIsRetryable:
    def test_overloaded_is_retryable(self) -> None:
        assert _is_retryable(Exception("AnthropicError - Overloaded")) is True

    def test_rate_limit_is_retryable(self) -> None:
        assert _is_retryable(Exception("rate_limit_error")) is True

    def test_timeout_is_retryable(self) -> None:
        assert _is_retryable(Exception("Request timeout")) is True

    def test_503_is_retryable(self) -> None:
        assert _is_retryable(Exception("ServiceUnavailableError 503")) is True

    def test_529_is_retryable(self) -> None:
        assert _is_retryable(Exception("529 overloaded")) is True

    def test_429_is_retryable(self) -> None:
        assert _is_retryable(Exception("429 too many requests")) is True

    def test_auth_error_not_retryable(self) -> None:
        assert _is_retryable(Exception("AuthenticationError: invalid API key")) is False

    def test_generic_error_not_retryable(self) -> None:
        assert _is_retryable(Exception("Model not found")) is False


# -- Retry tests ------------------------------------------------------------------


class TestChatRetry:
    @pytest.fixture
    def client(self) -> LLMClient:
        return LLMClient(LLMConfig(api_key="sk-test", model="gpt-4o"))

    @patch("backend.src.core.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_chat_retries_on_overloaded(self, mock_sleep: AsyncMock, client: LLMClient) -> None:
        """chat() should retry on transient errors and succeed."""
        ok_response = MagicMock()
        ok_response.choices = [MagicMock()]
        ok_response.choices[0].message.content = "Success"
        ok_response.usage = MagicMock(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        ok_response.model = "gpt-4o"

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_ac, \
             patch("backend.src.core.llm_client.litellm.completion_cost", return_value=0.0):
            mock_ac.side_effect = [
                Exception("AnthropicError - Overloaded"),
                ok_response,
            ]
            result = await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert result.content == "Success"
        assert mock_ac.call_count == 2
        mock_sleep.assert_called_once()

    @patch("backend.src.core.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_chat_no_retry_on_auth_error(self, mock_sleep: AsyncMock, client: LLMClient) -> None:
        """chat() should NOT retry on non-transient errors."""
        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = Exception("AuthenticationError: invalid API key")

            with pytest.raises(LLMError, match="LLM chat failed"):
                await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert mock_ac.call_count == 1
        mock_sleep.assert_not_called()

    @patch("backend.src.core.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_chat_exhausts_retries(self, mock_sleep: AsyncMock, client: LLMClient) -> None:
        """chat() should raise after exhausting retries."""
        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = Exception("503 Service Unavailable")

            with pytest.raises(LLMError, match="LLM chat failed"):
                await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert mock_ac.call_count == 4  # 1 initial + 3 retries


class TestStreamChatRetry:
    @pytest.fixture
    def client(self) -> LLMClient:
        return LLMClient(LLMConfig(api_key="sk-test", model="gpt-4o"))

    @patch("backend.src.core.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_stream_retries_on_initial_overloaded(self, mock_sleep: AsyncMock, client: LLMClient) -> None:
        """stream_chat() should retry when error occurs before any chunks are yielded."""
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "Hello"

        async def mock_stream():
            yield chunk

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = [
                Exception("AnthropicError - Overloaded"),
                mock_stream(),
            ]

            chunks: list[str] = []
            async for content in client.stream_chat(messages=[{"role": "user", "content": "Hi"}]):
                chunks.append(content)

        assert chunks == ["Hello"]
        assert mock_ac.call_count == 2
        mock_sleep.assert_called_once()

    @patch("backend.src.core.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_stream_no_retry_after_chunks_yielded(self, mock_sleep: AsyncMock, client: LLMClient) -> None:
        """stream_chat() should NOT retry when error occurs mid-stream."""
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "Partial"

        async def mock_stream():
            yield chunk
            raise Exception("AnthropicError - Overloaded")

        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.return_value = mock_stream()

            with pytest.raises(LLMError, match="mid-stream"):
                async for _ in client.stream_chat(messages=[{"role": "user", "content": "Hi"}]):
                    pass

        assert mock_ac.call_count == 1


class TestStructuredOutputRetry:
    @pytest.fixture
    def client(self) -> LLMClient:
        return LLMClient(LLMConfig(api_key="sk-test", model="gpt-4o"))

    @staticmethod
    def _make_stream(text: str):
        async def stream():
            for char in text:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = char
                yield chunk

        return stream()

    @patch("backend.src.core.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_structured_output_retries_on_overloaded(self, mock_sleep: AsyncMock, client: LLMClient) -> None:
        """structured_output() should retry on transient errors before streaming starts."""
        with patch("backend.src.core.llm_client.litellm.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = [
                Exception("529 Overloaded"),
                self._make_stream('{"result": "ok"}'),
            ]
            result = await client.structured_output(
                messages=[{"role": "user", "content": "Test"}],
                response_format={"type": "json_object"},
            )

        assert result == {"result": "ok"}
        assert mock_ac.call_count == 2
        mock_sleep.assert_called_once()
