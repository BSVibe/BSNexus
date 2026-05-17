"""Part B / PR 5 — dispatcher wires the per-project sandbox.

``dispatch_run_attempt`` acquires the project's sandbox session once
and threads it into the ToolRegistry. With sandbox_enabled false the
resolved manager is the host-side Noop, so behaviour is unchanged —
this test injects a counting manager to prove the wiring.
"""

from __future__ import annotations

import uuid

from backend.src.core.run_attempt_executor import _build_tool_registry, dispatch_run_attempt
from backend.src.core.sandbox import NoopSandboxManager
from tests.test_dispatcher_tool_loop import _ScriptedExecutor, _seed_request_with_step


class CountingSandboxManager:
    """Wraps NoopSandboxManager, recording every ``acquire``."""

    def __init__(self) -> None:
        self._inner = NoopSandboxManager()
        self.acquire_calls: list[tuple[uuid.UUID, str]] = []

    async def acquire(self, project_id, workspace_path):
        self.acquire_calls.append((project_id, workspace_path))
        return await self._inner.acquire(project_id, workspace_path)

    async def release(self, project_id):
        await self._inner.release(project_id)

    async def reap_idle(self):
        await self._inner.reap_idle()

    async def health(self):
        return await self._inner.health()


def test_build_tool_registry_threads_sandbox_session(tmp_path) -> None:
    session_obj = object()  # opaque stand-in — only identity is checked
    registry = _build_tool_registry(tmp_path, session_obj)  # type: ignore[arg-type]
    assert registry is not None
    assert registry._sandbox is session_obj


async def test_dispatch_acquires_project_sandbox_once(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
) -> None:
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [{"id": "c1", "name": "file_write", "arguments": {"path": "a.py", "content": "x = 1\n"}}],
        ],
        final_text="done",
    )
    mgr = CountingSandboxManager()

    await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        workspace_dir=tmp_path,
        sandbox_manager=mgr,
    )

    # Acquired exactly once — the per-project session is reused across
    # the continuation chain, not re-acquired per RunAttempt.
    assert len(mgr.acquire_calls) == 1
    assert mgr.acquire_calls[0][0] == request.project_id
    assert mgr.acquire_calls[0][1] == str(tmp_path)


async def test_dispatch_without_workspace_skips_acquire(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
) -> None:
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(final_text="Plain reply.")
    mgr = CountingSandboxManager()

    await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        workspace_dir=None,
        sandbox_manager=mgr,
    )
    assert mgr.acquire_calls == []
