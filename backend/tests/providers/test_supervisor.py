"""Tests for SupervisorProvider protocol and implementations.

TDD: Written BEFORE implementation code.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.src.providers.supervisor import (
    BSupervisorProvider,
    NoOpSupervisorProvider,
    SupervisorProvider,
)


# ---------------------------------------------------------------------------
# Protocol compliance tests
# ---------------------------------------------------------------------------
class TestSupervisorProviderProtocol:
    """Verify SupervisorProvider is a typing.Protocol with correct methods."""

    def test_protocol_is_runtime_checkable(self) -> None:
        """SupervisorProvider should be runtime_checkable."""
        assert hasattr(SupervisorProvider, "__protocol_attrs__") or hasattr(
            SupervisorProvider, "__abstractmethods__"
        ), "SupervisorProvider must be a Protocol"

    def test_compliant_class_is_instance(self) -> None:
        """A class with the right methods satisfies the protocol structurally."""

        class _FakeSupervisor:
            async def log_event(
                self,
                agent_id: str,
                event_type: str,
                data: dict[str, Any] | None = None,
            ) -> None:
                pass

            async def check_permission(
                self,
                agent_id: str,
                action: str,
            ) -> bool:
                return True

        assert isinstance(_FakeSupervisor(), SupervisorProvider)

    def test_non_compliant_class_is_not_instance(self) -> None:
        """A class missing methods does NOT satisfy the protocol."""

        class _Incomplete:
            pass

        assert not isinstance(_Incomplete(), SupervisorProvider)

    def test_partial_implementation_not_instance(self) -> None:
        """A class with only one of the two methods is not compliant."""

        class _OnlyLogEvent:
            async def log_event(self, agent_id: str, event_type: str, data: dict[str, Any] | None = None) -> None:
                pass

        assert not isinstance(_OnlyLogEvent(), SupervisorProvider)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mock_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    raise_error: httpx.HTTPStatusError | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_error:
        resp.raise_for_status.side_effect = raise_error
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# BSupervisorProvider tests
# ---------------------------------------------------------------------------
class TestBSupervisorProvider:
    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def provider(self, mock_client: AsyncMock) -> BSupervisorProvider:
        return BSupervisorProvider(
            base_url="https://supervisor.example.com",
            api_key="test-sv-key-1234",
            timeout=15.0,
            client=mock_client,
        )

    async def test_log_event_success(self, provider: BSupervisorProvider, mock_client: AsyncMock) -> None:
        """Should POST event to BSupervisor API."""
        mock_client.post = AsyncMock(return_value=_mock_response())

        await provider.log_event(
            agent_id="agent-001",
            event_type="task_started",
            data={"task_id": "t-123", "project_id": "p-456"},
        )

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://supervisor.example.com/api/v1/events"
        body = call_args[1].get("json", {})
        assert body["agent_id"] == "agent-001"
        assert body["event_type"] == "task_started"
        assert body["data"] == {"task_id": "t-123", "project_id": "p-456"}

    async def test_log_event_without_data(self, provider: BSupervisorProvider, mock_client: AsyncMock) -> None:
        """Should work when data is None."""
        mock_client.post = AsyncMock(return_value=_mock_response())

        await provider.log_event(agent_id="agent-001", event_type="heartbeat")

        call_args = mock_client.post.call_args
        body = call_args[1].get("json", {})
        assert body["data"] is None

    async def test_log_event_http_error(self, provider: BSupervisorProvider, mock_client: AsyncMock) -> None:
        """Should raise on HTTP errors from BSupervisor."""
        error_resp = MagicMock()
        error_resp.status_code = 500
        mock_client.post = AsyncMock(return_value=_mock_response(
            status_code=500,
            raise_error=httpx.HTTPStatusError("Internal Server Error", request=MagicMock(), response=error_resp),
        ))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.log_event(agent_id="agent-001", event_type="task_started")

    async def test_log_event_sends_bearer_auth(self, provider: BSupervisorProvider, mock_client: AsyncMock) -> None:
        """API key should be sent as Bearer token."""
        mock_client.post = AsyncMock(return_value=_mock_response())

        await provider.log_event(agent_id="agent-001", event_type="test")

        call_args = mock_client.post.call_args
        headers = call_args[1].get("headers", {})
        assert headers["Authorization"] == "Bearer test-sv-key-1234"

    async def test_check_permission_allowed(self, provider: BSupervisorProvider, mock_client: AsyncMock) -> None:
        """Should return True when BSupervisor allows the action."""
        mock_client.post = AsyncMock(return_value=_mock_response(json_data={"allowed": True}))

        result = await provider.check_permission(agent_id="agent-001", action="deploy")

        assert result is True
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://supervisor.example.com/api/v1/permissions/check"
        body = call_args[1].get("json", {})
        assert body["agent_id"] == "agent-001"
        assert body["action"] == "deploy"

    async def test_check_permission_denied(self, provider: BSupervisorProvider, mock_client: AsyncMock) -> None:
        """Should return False when BSupervisor denies the action."""
        mock_client.post = AsyncMock(return_value=_mock_response(json_data={"allowed": False}))

        result = await provider.check_permission(agent_id="agent-001", action="deploy")

        assert result is False

    async def test_check_permission_http_error(self, provider: BSupervisorProvider, mock_client: AsyncMock) -> None:
        """Should raise on HTTP errors from BSupervisor."""
        error_resp = MagicMock()
        error_resp.status_code = 403
        mock_client.post = AsyncMock(return_value=_mock_response(
            status_code=403,
            raise_error=httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=error_resp),
        ))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.check_permission(agent_id="agent-001", action="deploy")

    async def test_check_permission_sends_bearer_auth(
        self, provider: BSupervisorProvider, mock_client: AsyncMock
    ) -> None:
        """API key should be sent as Bearer token in permission checks."""
        mock_client.post = AsyncMock(return_value=_mock_response(json_data={"allowed": True}))

        await provider.check_permission(agent_id="agent-001", action="deploy")

        call_args = mock_client.post.call_args
        headers = call_args[1].get("headers", {})
        assert headers["Authorization"] == "Bearer test-sv-key-1234"


# ---------------------------------------------------------------------------
# NoOpSupervisorProvider tests
# ---------------------------------------------------------------------------
class TestNoOpSupervisorProvider:
    @pytest.fixture
    def provider(self) -> NoOpSupervisorProvider:
        return NoOpSupervisorProvider()

    async def test_log_event_does_nothing(self, provider: NoOpSupervisorProvider) -> None:
        """log_event should succeed silently (no-op)."""
        await provider.log_event(
            agent_id="agent-001",
            event_type="task_started",
            data={"task_id": "t-123"},
        )

    async def test_log_event_without_data(self, provider: NoOpSupervisorProvider) -> None:
        """log_event should work without data."""
        await provider.log_event(agent_id="agent-001", event_type="heartbeat")

    async def test_check_permission_always_true(self, provider: NoOpSupervisorProvider) -> None:
        """check_permission should always return True (permissive fallback)."""
        result = await provider.check_permission(agent_id="agent-001", action="deploy")
        assert result is True

    async def test_check_permission_always_true_for_any_action(self, provider: NoOpSupervisorProvider) -> None:
        """check_permission should return True regardless of action."""
        for action in ["deploy", "delete", "admin", "unknown_action"]:
            result = await provider.check_permission(agent_id="any-agent", action=action)
            assert result is True

    async def test_is_supervisor_provider_instance(self, provider: NoOpSupervisorProvider) -> None:
        """NoOpSupervisorProvider should satisfy SupervisorProvider protocol."""
        assert isinstance(provider, SupervisorProvider)

    async def test_bsupervisor_is_supervisor_provider_instance(self) -> None:
        """BSupervisorProvider should satisfy SupervisorProvider protocol."""
        mock_client = AsyncMock()
        provider = BSupervisorProvider(base_url="https://example.com", api_key="key", client=mock_client)
        assert isinstance(provider, SupervisorProvider)
