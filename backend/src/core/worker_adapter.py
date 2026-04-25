"""WorkerDispatchAdapter — orchestrator executor that hands a run to a
remote worker via Redis Streams.

Fits into the same protocol as ``LiteLLMOrchestratorAdapter``:

    async def execute(system_prompt, *, tools_allowed) -> dict

The dict it returns has ``status="dispatched"`` rather than ``"done"``.
``RunOrchestrator.dispatch_run`` treats that as a signal to STOP after
the run transitions to ``running``: the worker will execute the LLM
call locally (using its own ``claude``/``codex``/``opencode`` CLI and
the operator's own credentials), then push the final result onto
``runs:results``. A separate consumer finalises the run later.

This is the layering: LLM (claude CLI / litellm / …) is the bottom
layer; executor abstracts over where the LLM runs. A tenant that has
only registered a worker thus never triggers a backend-side LLM call.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


class WorkerDispatchAdapter:
    """Orchestrator-facing adapter that publishes runs to a worker."""

    # Tools the worker's coding CLIs typically handle. Consumed by
    # ``RunOrchestrator._tools_from_executor_hint`` for template scoring.
    tools_supported: list[str] = ["read", "write", "exec", "git"]

    def __init__(
        self,
        *,
        stream_manager: RedisStreamManager,
        worker_id: uuid.UUID,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        workspace_dir: str | None = None,
    ):
        self._stream = stream_manager
        self._worker_id = worker_id
        self._run_id = run_id
        self._project_id = project_id
        self._workspace_dir = workspace_dir
        self._dispatcher = WorkerDispatcher(stream_manager)

    async def execute(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        tools_allowed: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        msg_id = await self._dispatcher.dispatch_run(
            self._worker_id,
            self._run_id,
            str(self._project_id),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools_allowed=tools_allowed,
            workspace_dir=self._workspace_dir,
            history=history,
        )
        logger.info(
            "run_dispatched_to_worker_adapter",
            run_id=str(self._run_id),
            worker_id=str(self._worker_id),
            msg_id=msg_id,
        )
        # Sentinel: orchestrator keeps the run in ``running`` state until
        # the worker posts a final result on ``runs:results``.
        return {
            "status": "dispatched",
            "output_type": None,
            "output_ref": None,
            "actual_cost_cents": 0,
            "worker_id": str(self._worker_id),
            "stream_msg_id": msg_id,
        }
