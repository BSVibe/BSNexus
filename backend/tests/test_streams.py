"""Tests for RedisStreamManager."""

import json

import pytest
import redis.asyncio as aioredis
from unittest.mock import AsyncMock

from backend.src.queue.streams import RedisStreamManager


@pytest.fixture
def mock_redis() -> AsyncMock:
    r = AsyncMock()
    r.xgroup_create = AsyncMock()
    r.xadd = AsyncMock()
    r.xreadgroup = AsyncMock()
    r.xack = AsyncMock()
    r.xtrim = AsyncMock()
    return r


@pytest.fixture
def manager(mock_redis: AsyncMock) -> RedisStreamManager:
    return RedisStreamManager(mock_redis)


# ── initialize_streams ────────────────────────────────────────────────


async def test_initialize_streams_creates_group(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """xgroup_create is called for each stream/group pair."""
    mock_redis.xgroup_create.return_value = True

    await manager.initialize_streams()

    mock_redis.xgroup_create.assert_called_once_with(
        RedisStreamManager.TASKS_ESCALATION,
        RedisStreamManager.GROUP_ARCHITECT,
        id="0",
        mkstream=True,
    )


async def test_initialize_streams_ignores_busygroup(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """BUSYGROUP error is silently ignored (group already exists)."""
    mock_redis.xgroup_create.side_effect = aioredis.ResponseError(
        "BUSYGROUP Consumer Group name already exists"
    )

    # Should not raise
    await manager.initialize_streams()


async def test_initialize_streams_raises_other_errors(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """Non-BUSYGROUP ResponseError is propagated."""
    mock_redis.xgroup_create.side_effect = aioredis.ResponseError("ERR some other error")

    with pytest.raises(aioredis.ResponseError, match="some other error"):
        await manager.initialize_streams()


# ── publish ───────────────────────────────────────────────────────────


async def test_publish_encodes_dict_values(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """Dict and list values are JSON-encoded; strings pass through."""
    mock_redis.xadd.return_value = b"1-0"

    await manager.publish("test-stream", {
        "simple": "hello",
        "nested": {"a": 1},
        "items": [1, 2, 3],
        "number": 42,
    })

    call_args = mock_redis.xadd.call_args
    flat_data = call_args[0][1]

    assert flat_data["simple"] == "hello"
    assert flat_data["nested"] == json.dumps({"a": 1})
    assert flat_data["items"] == json.dumps([1, 2, 3])
    assert flat_data["number"] == "42"


async def test_publish_calls_xadd_with_flattened_data(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """publish flattens data and passes correct stream/data to xadd."""
    mock_redis.xadd.return_value = b"1234-0"

    result = await manager.publish("my:stream", {"key": "val", "nested": {"a": 1}})

    mock_redis.xadd.assert_awaited_once_with("my:stream", {"key": "val", "nested": json.dumps({"a": 1})})
    assert result == b"1234-0"


# ── consume ───────────────────────────────────────────────────────────


async def test_consume_parses_json_values(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """JSON strings in messages are decoded to Python objects."""
    mock_redis.xreadgroup.return_value = [
        (b"tasks:escalation", [
            (b"1-0", {b"task_id": b'"abc"', b"count": b"5", b"meta": b'{"x": 1}'}),
        ]),
    ]

    results = await manager.consume("tasks:escalation", "architect", "worker-1")

    assert len(results) == 1
    msg = results[0]
    assert msg[b"task_id"] == "abc"
    assert msg[b"meta"] == {"x": 1}


async def test_consume_handles_decode_error(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """Invalid JSON is kept as-is (raw bytes)."""
    mock_redis.xreadgroup.return_value = [
        (b"tasks:escalation", [
            (b"1-0", {b"bad": b"not{json"}),
        ]),
    ]

    results = await manager.consume("tasks:escalation", "architect", "worker-1")

    assert results[0][b"bad"] == b"not{json"


async def test_consume_adds_message_id(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """_message_id field is injected into each parsed message."""
    mock_redis.xreadgroup.return_value = [
        (b"stream", [(b"99-0", {b"k": b'"v"'})]),
    ]

    results = await manager.consume("stream", "g", "c")

    assert results[0]["_message_id"] == b"99-0"


async def test_consume_empty_returns_empty_list(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """No messages (None or empty) returns an empty list."""
    mock_redis.xreadgroup.return_value = None

    assert await manager.consume("s", "g", "c") == []

    mock_redis.xreadgroup.return_value = []
    assert await manager.consume("s", "g", "c") == []


# ── acknowledge ───────────────────────────────────────────────────────


async def test_acknowledge_calls_xack(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """xack is called with correct stream, group, and message_id."""
    await manager.acknowledge("my-stream", "my-group", "1-0")

    mock_redis.xack.assert_awaited_once_with("my-stream", "my-group", "1-0")


# ── publish_board_event ───────────────────────────────────────────────


async def test_publish_board_event_formats_correctly(manager: RedisStreamManager, mock_redis: AsyncMock) -> None:
    """Event name is included in the data dict published to EVENTS_BOARD."""
    mock_redis.xadd.return_value = b"1-0"

    await manager.publish_board_event("task_moved", {"task_id": "abc"})

    call_args = mock_redis.xadd.call_args
    stream = call_args[0][0]
    flat_data = call_args[0][1]

    assert stream == RedisStreamManager.EVENTS_BOARD
    assert flat_data["event"] == "task_moved"
    assert flat_data["task_id"] == "abc"


# ── trim_streams ──────────────────────────────────────────────────────


async def test_trim_streams_calls_xtrim_with_correct_maxlen(
    manager: RedisStreamManager, mock_redis: AsyncMock
) -> None:
    """xtrim uses caller maxlen for escalation, hardcoded 5000 for board."""
    await manager.trim_streams(maxlen=500)

    assert mock_redis.xtrim.call_count == 2

    calls = {c.args[0]: c.kwargs for c in mock_redis.xtrim.call_args_list}
    assert calls[RedisStreamManager.TASKS_ESCALATION]["maxlen"] == 500
    assert calls[RedisStreamManager.EVENTS_BOARD]["maxlen"] == 5000
