"""NotificationProvider protocol and implementations.

Self-contained module -- no cross-provider imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class NotificationResult:
    sent: bool
    channel: str | None = None
    error: str | None = None


@runtime_checkable
class NotificationProvider(Protocol):
    async def send(
        self,
        message: str,
        channel: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NotificationResult: ...

    async def send_briefing(
        self,
        suggestions: list[dict],
        project_id: str,
    ) -> NotificationResult: ...

    async def send_status(self, status: dict[str, Any]) -> NotificationResult: ...


class BSageNotificationProvider:
    """Routes notifications through BSage POST /api/notify."""

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

    async def send(
        self,
        message: str,
        channel: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NotificationResult:
        """Send notification through BSage notify API."""
        body: dict[str, Any] = {"message": message}
        if channel:
            body["channel"] = channel
        if metadata:
            body["metadata"] = metadata
        try:
            resp = await self._client.post(
                f"{self._base_url}/api/notify",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return NotificationResult(sent=data["sent"], channel=data.get("channel"))
        except Exception as exc:
            logger.error("notification_send_failed", error=str(exc))
            return NotificationResult(sent=False, error=str(exc))

    async def send_briefing(
        self,
        suggestions: list[dict],
        project_id: str,
    ) -> NotificationResult:
        """Format and send morning briefing."""
        if not suggestions:
            return await self.send("No suggestions available.")

        lines = [f"Today's task suggestions ({project_id})\n"]
        for i, s in enumerate(suggestions, 1):
            lines.append(f"{i}. {s.get('title', 'Untitled')}")
            if s.get("task_type"):
                lines.append(f"   type: {s['task_type']}")
            if s.get("reasoning"):
                lines.append(f"   {s['reasoning']}")
        return await self.send("\n".join(lines))

    async def send_status(self, status: dict[str, Any]) -> NotificationResult:
        """Format and send status update."""
        msg = (
            f"Task status\n"
            f"Active tasks: {status.get('active_tasks', 0)}\n"
            f"Pending suggestions: {status.get('pending_suggestions', 0)}\n"
            f"Approved today: {status.get('approved_today', 0)}"
        )
        return await self.send(msg)

    async def close(self) -> None:
        await self._client.aclose()


class NoOpNotificationProvider:
    """No-op fallback -- silently discards all notifications."""

    async def send(
        self,
        message: str,
        channel: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NotificationResult:
        return NotificationResult(sent=False, error="No notification provider configured")

    async def send_briefing(
        self,
        suggestions: list[dict],
        project_id: str,
    ) -> NotificationResult:
        return NotificationResult(sent=False, error="No notification provider configured")

    async def send_status(self, status: dict[str, Any]) -> NotificationResult:
        return NotificationResult(sent=False, error="No notification provider configured")
