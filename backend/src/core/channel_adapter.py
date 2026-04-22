"""Channel adapters — fan project chat events out to external channels.

Phase 8 in the overhaul roadmap. Adapters tail the per-project Redis
Stream that ``agent_chat`` already publishes to, format each new
message for the destination channel, and post it via the channel's
SDK / webhook.

The Slack adapter ships first because the chat persistence layer was
already designed with it in mind (``ConversationMessage.source``,
``external_id``, ``thread_ref``). Discord / Teams adapters follow the
same pattern: subclass ``ChannelAdapter``, implement ``post_message``,
and register the kind in ``CHANNEL_ADAPTER_REGISTRY``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Protocol

import httpx
import structlog

from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


class ChannelAdapter(Protocol):
    kind: str

    async def post_message(self, channel: "ChannelTarget", text: str, *, role: str, agent_name: str | None) -> None: ...


class ChannelTarget:
    """Plain dataclass-ish target so adapters don't need the ORM model."""

    def __init__(
        self,
        *,
        kind: str,
        external_channel_id: str,
        credentials: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind
        self.external_channel_id = external_channel_id
        self.credentials = credentials or {}


class SlackChannelAdapter:
    """Post chat messages to a Slack channel via the Web API.

    Credentials must include a ``bot_token`` (xoxb-…). The adapter does
    not need the channel name — Slack accepts the channel id directly.
    """

    kind = "slack"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(timeout=10.0)

    async def post_message(
        self,
        channel: ChannelTarget,
        text: str,
        *,
        role: str,
        agent_name: str | None,
    ) -> None:
        token = channel.credentials.get("bot_token")
        if not token:
            logger.warning("slack_missing_bot_token", channel=channel.external_channel_id)
            return

        prefix = ""
        if role == "assistant" and agent_name:
            prefix = f"*{agent_name}* — "
        body = {
            "channel": channel.external_channel_id,
            "text": f"{prefix}{text}"[:39000],
        }
        try:
            resp = await self._http.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
            payload = resp.json()
            if not payload.get("ok"):
                logger.warning(
                    "slack_post_failed",
                    channel=channel.external_channel_id,
                    error=payload.get("error"),
                )
        except Exception:
            logger.exception("slack_post_exception", channel=channel.external_channel_id)


CHANNEL_ADAPTER_REGISTRY: dict[str, type[ChannelAdapter]] = {
    "slack": SlackChannelAdapter,  # type: ignore[dict-item]
}


# ── Background fan-out task ─────────────────────────────────────────


class ChannelFanout:
    """Tail one project's chat stream and forward messages to its channels."""

    def __init__(
        self,
        project_id: uuid.UUID,
        targets: list[ChannelTarget],
        stream: RedisStreamManager,
    ) -> None:
        self.project_id = project_id
        self.targets = targets
        self._stream = stream
        self._stop = asyncio.Event()
        self._adapters: dict[str, ChannelAdapter] = {}

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        stream_key = RedisStreamManager.chat_events_stream(str(self.project_id))
        last_id = "$"
        while not self._stop.is_set():
            try:
                entries = await self._stream.tail(stream_key, last_id=last_id, block=15000)
            except Exception:
                logger.exception("chat_tail_failed", project_id=str(self.project_id))
                await asyncio.sleep(1)
                continue
            # Yield to the event loop so cooperative cancellation can fire
            # even when tail() returns instantly (e.g. tests with mocked
            # streams or genuinely empty Redis streams).
            if not entries:
                await asyncio.sleep(0)
            for entry in entries:
                last_id = entry.pop("_message_id", last_id)
                if entry.get("event") != "message_created":
                    continue
                data = entry.get("data") or {}
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                content = str(data.get("content") or "")
                if not content:
                    continue
                role = str(data.get("role") or "assistant")
                agent_name = data.get("agent_name")
                for target in self.targets:
                    adapter = self._get_adapter(target.kind)
                    if adapter is None:
                        continue
                    await adapter.post_message(target, content, role=role, agent_name=agent_name)

    def _get_adapter(self, kind: str) -> ChannelAdapter | None:
        if kind not in self._adapters:
            cls = CHANNEL_ADAPTER_REGISTRY.get(kind)
            if cls is None:
                return None
            self._adapters[kind] = cls()
        return self._adapters[kind]
