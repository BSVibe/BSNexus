"""Tests for NotificationProvider implementations."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.src.providers.notification import (
    BSageNotificationProvider,
    NoOpNotificationProvider,
    NotificationProvider,
    NotificationResult,
)

pytestmark = pytest.mark.asyncio


# -- NotificationResult -------------------------------------------------------


class TestNotificationResult:
    def test_defaults(self) -> None:
        result = NotificationResult(sent=True)
        assert result.sent is True
        assert result.channel is None
        assert result.error is None

    def test_with_all_fields(self) -> None:
        result = NotificationResult(sent=False, channel="telegram", error="timeout")
        assert result.sent is False
        assert result.channel == "telegram"
        assert result.error == "timeout"


# -- Protocol conformance -----------------------------------------------------


class TestProtocolConformance:
    def test_bsage_implements_protocol(self) -> None:
        provider = BSageNotificationProvider(base_url="http://localhost", api_key="k")
        assert isinstance(provider, NotificationProvider)

    def test_noop_implements_protocol(self) -> None:
        provider = NoOpNotificationProvider()
        assert isinstance(provider, NotificationProvider)


# -- BSageNotificationProvider.send -------------------------------------------


class TestBSageSend:
    async def test_send_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sent": True, "channel": "telegram"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        provider = BSageNotificationProvider(
            base_url="http://bsage.test",
            api_key="test-key",
            client=mock_client,
        )

        result = await provider.send("hello")

        assert result.sent is True
        assert result.channel == "telegram"
        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.args[0] == "http://bsage.test/api/notify"
        assert call_kwargs.kwargs["json"] == {"message": "hello"}
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-key"

    async def test_send_with_channel_and_metadata(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"sent": True}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        provider = BSageNotificationProvider(
            base_url="http://bsage.test/",
            api_key="k",
            client=mock_client,
        )

        result = await provider.send("msg", channel="slack", metadata={"key": "val"})

        assert result.sent is True
        body = mock_client.post.call_args.kwargs["json"]
        assert body["channel"] == "slack"
        assert body["metadata"] == {"key": "val"}

    async def test_send_strips_trailing_slash_from_base_url(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"sent": True}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        provider = BSageNotificationProvider(
            base_url="http://bsage.test///",
            api_key="k",
            client=mock_client,
        )

        await provider.send("test")

        url = mock_client.post.call_args.args[0]
        assert url == "http://bsage.test/api/notify"

    async def test_send_http_error_returns_failure(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        ))

        provider = BSageNotificationProvider(
            base_url="http://bsage.test",
            api_key="k",
            client=mock_client,
        )

        result = await provider.send("hello")

        assert result.sent is False
        assert result.error is not None
        assert "Server Error" in result.error

    async def test_send_connection_error_returns_failure(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        provider = BSageNotificationProvider(
            base_url="http://bsage.test",
            api_key="k",
            client=mock_client,
        )

        result = await provider.send("hello")

        assert result.sent is False
        assert "Connection refused" in (result.error or "")


# -- BSageNotificationProvider.send_briefing ----------------------------------


class TestBSageSendBriefing:
    async def test_send_briefing_formats_suggestions(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"sent": True}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        provider = BSageNotificationProvider(
            base_url="http://bsage.test",
            api_key="k",
            client=mock_client,
        )

        suggestions = [
            {"title": "Fix login bug", "task_type": "bugfix", "reasoning": "Users report 500"},
            {"title": "Add caching"},
        ]

        result = await provider.send_briefing(suggestions, "proj-1")

        assert result.sent is True
        body = mock_client.post.call_args.kwargs["json"]
        msg = body["message"]
        assert "proj-1" in msg
        assert "1. Fix login bug" in msg
        assert "type: bugfix" in msg
        assert "Users report 500" in msg
        assert "2. Add caching" in msg

    async def test_send_briefing_empty_suggestions(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"sent": True}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        provider = BSageNotificationProvider(
            base_url="http://bsage.test",
            api_key="k",
            client=mock_client,
        )

        result = await provider.send_briefing([], "proj-1")

        assert result.sent is True
        body = mock_client.post.call_args.kwargs["json"]
        assert "No suggestions" in body["message"]


# -- BSageNotificationProvider.send_status ------------------------------------


class TestBSageSendStatus:
    async def test_send_status_formats_correctly(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"sent": True}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        provider = BSageNotificationProvider(
            base_url="http://bsage.test",
            api_key="k",
            client=mock_client,
        )

        status = {"active_tasks": 3, "pending_suggestions": 5, "approved_today": 2}
        result = await provider.send_status(status)

        assert result.sent is True
        body = mock_client.post.call_args.kwargs["json"]
        msg = body["message"]
        assert "Active tasks: 3" in msg
        assert "Pending suggestions: 5" in msg
        assert "Approved today: 2" in msg

    async def test_send_status_defaults_missing_fields(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"sent": True}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        provider = BSageNotificationProvider(
            base_url="http://bsage.test",
            api_key="k",
            client=mock_client,
        )

        result = await provider.send_status({})

        assert result.sent is True
        body = mock_client.post.call_args.kwargs["json"]
        msg = body["message"]
        assert "Active tasks: 0" in msg


# -- BSageNotificationProvider.close ------------------------------------------


class TestBSageClose:
    async def test_close_calls_aclose(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        provider = BSageNotificationProvider(
            base_url="http://bsage.test",
            api_key="k",
            client=mock_client,
        )

        await provider.close()

        mock_client.aclose.assert_awaited_once()


# -- NoOpNotificationProvider -------------------------------------------------


class TestNoOpNotificationProvider:
    async def test_send_returns_not_sent(self) -> None:
        provider = NoOpNotificationProvider()
        result = await provider.send("hello")
        assert result.sent is False
        assert result.error is not None

    async def test_send_briefing_returns_not_sent(self) -> None:
        provider = NoOpNotificationProvider()
        result = await provider.send_briefing([{"title": "Task"}], "proj-1")
        assert result.sent is False
        assert result.error is not None

    async def test_send_status_returns_not_sent(self) -> None:
        provider = NoOpNotificationProvider()
        result = await provider.send_status({"active_tasks": 1})
        assert result.sent is False
        assert result.error is not None


# -- Dependency factory -------------------------------------------------------


class TestNotificationDependency:
    def test_create_bsage_provider(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.notification_provider = "bsage"
            mock_settings.bsage_notification_url = "http://bsage.test"
            mock_settings.bsage_url = ""
            mock_settings.bsage_api_key = "test-key"

            from backend.src.providers.dependencies import create_notification_provider

            provider = create_notification_provider()

            assert isinstance(provider, BSageNotificationProvider)

    def test_create_bsage_falls_back_to_bsage_url(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.notification_provider = "bsage"
            mock_settings.bsage_notification_url = ""
            mock_settings.bsage_url = "http://bsage-fallback.test"
            mock_settings.bsage_api_key = "test-key"

            from backend.src.providers.dependencies import create_notification_provider

            provider = create_notification_provider()

            assert isinstance(provider, BSageNotificationProvider)
            assert provider._base_url == "http://bsage-fallback.test"

    def test_create_noop_by_default(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.notification_provider = "noop"

            from backend.src.providers.dependencies import create_notification_provider

            provider = create_notification_provider()

            assert isinstance(provider, NoOpNotificationProvider)

    def test_get_notification_provider_returns_instance(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.notification_provider = "noop"

            from backend.src.providers.dependencies import get_notification_provider

            # Clear cache for test isolation
            get_notification_provider.cache_clear()
            provider = get_notification_provider()

            assert isinstance(provider, NotificationProvider)
            get_notification_provider.cache_clear()
