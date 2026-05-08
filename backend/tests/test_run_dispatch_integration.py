"""Round-trip integration tests for the message → run → response chain.

Stage 4 against production surfaced two prod-only bugs that the
existing test surface missed despite 458 green tests:

  3. ``DirectLLMAdapter`` returned ``output_ref`` as a bare string,
     so every ``GET /api/v1/runs?request_id={id}`` 500'd on
     ``ResponseValidationError``.
  4. ``RunOrchestrator._on_run_completed`` unconditionally spawned a
     child run, producing a 1000-deep chain that only ollama crashing
     terminated.

Both classes were invisible to:

  * Unit tests of ``DirectLLMAdapter`` that asserted the *internal*
    result dict shape with mocked ``acompletion``.
  * Tests of the response model that hand-shaped well-formed inputs.
  * The orchestrator e2e test, which mocked the executor and didn't
    assert "no children spawned".

These integration tests close the structural gap by *round-tripping*
through the real router, real DB, real ``DirectLLMAdapter``, and real
response model — only the LLM provider is stubbed (via
``acompletion`` patch) so the test is fast and deterministic.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.src.models import ExecutionRun, ExecutorConfig, Project


@pytest_asyncio.fixture
async def patched_async_session(test_session_maker):
    """Redirect the *module-level* ``async_session`` factory to the
    test engine.

    The dispatcher's background task opens its own session via
    ``async with async_session() as s`` (not via FastAPI's ``Depends``),
    so the request-scoped ``dependency_overrides`` don't reach it.
    Patch the symbol in every module that imports it so the
    background dispatcher sees the in-memory SQLite DB.
    """
    targets = [
        "backend.src.storage.database.async_session",
        "backend.src.core.dispatcher.async_session",
    ]
    with patch(targets[0], test_session_maker), patch(targets[1], test_session_maker):
        yield


def _delta_chunk(content: str = "", finish_reason: str | None = None) -> Any:
    """Minimal litellm streaming chunk."""
    delta = MagicMock()
    delta.content = content if content else None
    delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


async def _async_iter(items: list[Any]):
    for item in items:
        yield item


@asynccontextmanager
async def _no_mcp_session(_self=None):
    """Bypass real MCP — the run-scoped HMAC token + streamable-HTTP
    client aren't in scope for this test. Patches
    ``DirectLLMAdapter._mcp_session`` (a bound method) so the
    ``self`` argument arrives here as ``_self`` and we ignore it.
    The adapter falls through the no-MCP branch and runs the LLM
    tool loop without tools — same code path as a tenant with no
    MCP server registered."""
    yield None


async def _drain_dispatch_tasks() -> None:
    """Wait until all fire-and-forget dispatch tasks settle.

    ``api/conversation.py`` keeps a strong-ref ``set[Task]`` so the GC
    doesn't reap mid-await; reading that set lets the test deterministically
    wait for the orchestrator's compose → execute → finalize chain to land
    in the DB before the assertions run.
    """
    from backend.src.api.conversation import _BACKGROUND_DISPATCH_TASKS  # noqa: PLC0415

    deadline = asyncio.get_event_loop().time() + 10.0
    while _BACKGROUND_DISPATCH_TASKS and asyncio.get_event_loop().time() < deadline:
        await asyncio.gather(*list(_BACKGROUND_DISPATCH_TASKS), return_exceptions=True)
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_message_to_run_roundtrip_response_model_passes(
    client,
    db_session,
    seeded_tenant,
    mock_tenant_id,
    patched_async_session,
):
    """POST /messages → real DirectLLMAdapter (acompletion stubbed) →
    DB write → GET /api/v1/runs?request_id={id}.

    Bug 3 regression: if the adapter writes ``output_ref`` as a bare
    string, GET 500s on ResponseValidationError. The test asserts 200
    + dict-shaped output_ref so a future producer-side regression
    fails this test, not production.
    """
    # ── Seed: project + selected llm_api executor.
    project = Project(tenant_id=mock_tenant_id, name="rt", description="")
    db_session.add(project)
    await db_session.flush()

    cfg = ExecutorConfig(
        tenant_id=mock_tenant_id,
        name="ollama-stub",
        executor_type="llm_api",
        config={
            "model": "ollama/qwen3-coder:30b",
            "base_url": "http://stub:11434",
            "api_key": "stub",
        },
        is_selected=True,
    )
    db_session.add(cfg)
    await db_session.commit()

    # ── Stub litellm.acompletion with a 2-chunk stream that matches the
    #    real ollama wire format closely enough.
    stream = _async_iter(
        [
            _delta_chunk(content="PONG"),
            _delta_chunk(finish_reason="stop"),
        ]
    )
    fake_acompletion = AsyncMock(return_value=stream)

    with patch("backend.src.core.llm.direct_client.acompletion", fake_acompletion):
        # MCP is patched out — the production path will work the same
        # way for runs whose tenant has no MCP server registered, and we
        # don't have one in the integration fixture.
        with patch(
            "backend.src.core.llm.direct_client.DirectLLMAdapter._mcp_session",
            _no_mcp_session,
        ):
            # ── POST a user message; orchestrator dispatches in background.
            send_res = await client.post(
                "/api/v1/messages",
                json={"content": "ping", "project_id": str(project.id)},
            )
            assert send_res.status_code == 201, send_res.text
            body = send_res.json()
            request_id = body["request_id"]
            assert request_id, body

            # Wait for the fire-and-forget dispatch task to land in DB.
            await _drain_dispatch_tasks()

    # ── GET /runs hits the strict response_model. Pre-PR #53 this 500'd.
    get_res = await client.get(f"/api/v1/runs?request_id={request_id}")
    assert get_res.status_code == 200, (
        f"GET /runs 500'd — likely a producer / response_model shape divergence. Body: {get_res.text}"
    )
    runs = get_res.json()
    assert len(runs) >= 1
    run = runs[0]
    assert run["status"] == "done"
    assert run["output_type"] == "text"

    # Bug 3 specific assertion — output_ref must be the dict
    # ``{"inline": "..."}``, NOT a bare string. Pydantic would already
    # have 500'd above, but pin the contract explicitly.
    assert isinstance(run["output_ref"], dict)
    assert run["output_ref"].get("inline") == "PONG"


@pytest.mark.asyncio
async def test_message_dispatch_does_not_spawn_child_runs(
    client,
    db_session,
    seeded_tenant,
    mock_tenant_id,
    patched_async_session,
):
    """Bug 4 regression: a single message → request → exactly one
    ExecutionRun, no children. Production saw 1001 chained runs when
    ``_on_run_completed`` always spawned a child."""
    project = Project(tenant_id=mock_tenant_id, name="no-loop", description="")
    db_session.add(project)
    await db_session.flush()

    cfg = ExecutorConfig(
        tenant_id=mock_tenant_id,
        name="ollama-stub",
        executor_type="llm_api",
        config={
            "model": "ollama/qwen3-coder:30b",
            "base_url": "http://stub:11434",
            "api_key": "stub",
        },
        is_selected=True,
    )
    db_session.add(cfg)
    await db_session.commit()

    stream = _async_iter(
        [
            _delta_chunk(content="PONG"),
            _delta_chunk(finish_reason="stop"),
        ]
    )
    fake_acompletion = AsyncMock(return_value=stream)

    with (
        patch("backend.src.core.llm.direct_client.acompletion", fake_acompletion),
        patch(
            "backend.src.core.llm.direct_client.DirectLLMAdapter._mcp_session",
            _no_mcp_session,
        ),
    ):
        send_res = await client.post(
            "/api/v1/messages",
            json={"content": "single dispatch", "project_id": str(project.id)},
        )
        assert send_res.status_code == 201
        await _drain_dispatch_tasks()

    # ── Real DB query (not via API) — the API filters by request_id and
    #    we want to see the entire ExecutionRun graph for this project.
    rows = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.project_id == project.id))).scalars().all()
    assert len(rows) == 1, (
        f"Expected exactly 1 run for one POST /messages; got {len(rows)}. "
        f"Re-introduced unconditional child spawn? See "
        f"run_orchestrator._on_run_completed for the retired Phase 0 spawn."
    )
    assert rows[0].parent_run_id is None
