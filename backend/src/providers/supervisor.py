"""SupervisorProvider protocol and implementations.

Self-contained module — no cross-provider imports.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

logger = structlog.get_logger(__name__)


@runtime_checkable
class SupervisorProvider(Protocol):
    """Agent supervisor interface using structural subtyping."""

    async def log_event(
        self,
        agent_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None: ...

    async def check_permission(
        self,
        agent_id: str,
        action: str,
    ) -> bool: ...


class BSupervisorProvider:
    """Routes supervisor calls through BSupervisor HTTP API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def log_event(
        self,
        agent_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Send an event to BSupervisor API."""
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "event_type": event_type,
            "data": data,
        }

        logger.info("bsupervisor_log_event", agent_id=agent_id, event_type=event_type)

        resp = await self._client.post(
            f"{self._base_url}/api/v1/events",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()

    async def check_permission(
        self,
        agent_id: str,
        action: str,
    ) -> bool:
        """Check if an agent is permitted to perform an action."""
        body: dict[str, str] = {
            "agent_id": agent_id,
            "action": action,
        }

        logger.info("bsupervisor_check_permission", agent_id=agent_id, action=action)

        resp = await self._client.post(
            f"{self._base_url}/api/v1/permissions/check",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()

        return resp.json()["allowed"]


class NoOpSupervisorProvider:
    """No-op fallback — logs nothing, always allows all actions."""

    async def log_event(
        self,
        agent_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """No-op: silently ignores the event."""

    async def check_permission(
        self,
        agent_id: str,
        action: str,
    ) -> bool:
        """No-op: always returns True (permissive fallback)."""
        return True
