import json

import redis.asyncio as redis


class RedisStreamManager:
    """Redis Streams abstraction layer."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self.redis = redis_client

    async def initialize_streams(self) -> None:
        """Initialize Redis Streams that need pre-created consumer groups.

        Per-project SSE streams (chat events, project events) and
        per-worker run streams are created lazily on first publish —
        no pre-creation needed. There are currently no globally-shared
        consumer-group streams, so this is a no-op kept for the
        startup hook contract.
        """
        return

    async def publish(self, stream: str, data: dict) -> str:
        """Publish a message to a stream."""
        flat_data: dict[str, str] = {
            str(k): json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in data.items()
        }
        message_id = await self.redis.xadd(stream, flat_data)  # type: ignore[arg-type]
        return message_id

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 1,
        block: int = 30000,  # 30 seconds
    ) -> list[dict]:
        """Consume messages from a consumer group."""
        messages = await self.redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=count,
            block=block,
        )

        results: list[dict] = []
        if messages:
            for stream_name, stream_messages in messages:
                for message_id, data in stream_messages:
                    parsed: dict = {}
                    for k, v in data.items():
                        try:
                            parsed[k] = json.loads(v)
                        except (json.JSONDecodeError, TypeError):
                            parsed[k] = v
                    parsed["_message_id"] = message_id
                    results.append(parsed)

        return results

    async def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        """Acknowledge message processing completion."""
        await self.redis.xack(stream, group, message_id)

    async def publish_project_event(self, project_id: str, event: str, data: dict) -> None:
        """Publish a per-project event (run transition, deliverable, decision, message).

        Wraps the payload in {event, data} so SSE consumers can dispatch on
        event type without mixing fields with metadata.
        """
        await self.publish(self.project_events_stream(project_id), {"event": event, "data": data})

    async def trim_stream(self, stream: str, maxlen: int = 1000) -> None:
        """Trim old messages from one named stream. Caller picks the
        retention budget per-stream — no globally shared streams to
        trim in bulk."""
        await self.redis.xtrim(stream, maxlen=maxlen, approximate=True)

    async def tail(self, stream: str, last_id: str = "$", block: int = 15000) -> list[dict]:
        """Tail a stream from `last_id`. Returns parsed messages with `_message_id`.

        Pass `$` to wait for new messages only. After receiving, pass the last
        `_message_id` back in for subsequent calls. Used by SSE fan-out — no
        consumer group, no ack, multiple subscribers can tail independently.
        """
        messages = await self.redis.xread({stream: last_id}, count=50, block=block)
        results: list[dict] = []
        if messages:
            for _stream_name, stream_messages in messages:
                for message_id, data in stream_messages:
                    parsed: dict = {}
                    for k, v in data.items():
                        try:
                            parsed[k] = json.loads(v)
                        except (json.JSONDecodeError, TypeError):
                            parsed[k] = v
                    parsed["_message_id"] = message_id
                    results.append(parsed)
        return results

    @staticmethod
    def chat_events_stream(project_id: str) -> str:
        """Stream key for project chat events (one stream per project)."""
        return f"chat:events:{project_id}"

    @staticmethod
    def project_events_stream(project_id: str) -> str:
        """Stream key for per-project SSE events (one stream per project).

        Carries run transitions, new deliverables, new/resolved
        decisions, and new chat messages — anything the Direction /
        Progress / Decisions / Inside views need to update in real
        time.
        """
        return f"project:events:{project_id}"
