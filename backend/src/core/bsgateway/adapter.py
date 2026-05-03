"""``BSGatewayAdapter`` — orchestrator-facing wrapper around ``BSGatewayClient``.

Exposes the same ``execute(system_prompt, user_prompt, *, tools_allowed,
history)`` surface that the previous ``LiteLLMOrchestratorAdapter``
exposed, so ``RunOrchestrator`` switches over without changing its call
sites. The translation is mechanical: assemble ``messages`` (system +
filtered history + user), pass through ``run_audit_metadata`` as the
BSGateway metadata bag, append ``workspace_dir`` / ``mcp_servers`` if
the orchestrator has plumbed them.

``tools_allowed`` is currently informational — BSGateway forwards
in-band OpenAI tools to executor CLIs only after TODO E5b. Tools used
by the worker's own CLI (claude's filesystem ops etc.) live inside the
worker harness and don't ride this wire.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from backend.src.core.bsgateway.client import BSGatewayClient


class _ChatExecutor(Protocol):
    async def execute(self, **kwargs: Any) -> dict[str, Any]: ...


class BSGatewayAdapter:
    """Wraps :class:`BSGatewayClient` to satisfy the orchestrator's
    executor protocol.

    Stateful only for ``run_audit_metadata`` / ``workspace_dir`` /
    ``mcp_servers`` because the dispatcher builds the adapter before
    the per-run audit dict is known and patches it in just before
    ``execute`` (this matches the pre-2026-05-03
    ``LiteLLMOrchestratorAdapter.set_run_audit_metadata`` lifecycle).
    """

    tools_supported: list[str] = [
        "file_read",
        "file_write",
        "file_list",
        "shell_exec",
    ]

    def __init__(
        self,
        *,
        client: _ChatExecutor | BSGatewayClient,
        model: str,
        project_id: uuid.UUID,
        run_audit_metadata: dict[str, Any] | None = None,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        on_chunk: Any | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._project_id = project_id
        self._run_audit_metadata: dict[str, Any] = dict(run_audit_metadata) if run_audit_metadata else {}
        self._workspace_dir = workspace_dir
        self._mcp_servers = mcp_servers
        self._on_chunk = on_chunk

    def set_run_audit_metadata(self, metadata: dict[str, Any] | None) -> None:
        """Replace the metadata bag sent to BSGateway on subsequent ``execute``."""
        self._run_audit_metadata = dict(metadata) if metadata else {}

    def set_workspace_dir(self, workspace_dir: str | None) -> None:
        self._workspace_dir = workspace_dir

    def set_mcp_servers(self, mcp_servers: dict[str, Any] | None) -> None:
        self._mcp_servers = mcp_servers

    def set_on_chunk(self, on_chunk: Any | None) -> None:
        """Callback fired for every ``delta.content`` chunk. Used by the
        dispatcher to publish run_output events to the Inside panel SSE."""
        self._on_chunk = on_chunk

    async def execute(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        tools_allowed: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for turn in history or []:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_prompt})

        kwargs: dict[str, Any] = dict(
            messages=messages,
            metadata=self._run_audit_metadata,
            model=self._model,
            workspace_dir=self._workspace_dir,
            mcp_servers=self._mcp_servers,
        )
        if self._on_chunk is not None:
            kwargs["on_chunk"] = self._on_chunk
        return await self._client.execute(**kwargs)
