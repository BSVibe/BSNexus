"""Executor factory — single entry point for all LLM calls.

Principle (#39, user): every LLM invocation must go through this
factory so that ``executor_configs.executor_type`` is actually honored.
Direct ``LiteLLMExecutor()`` / ``ClaudeCodeExecutor()`` construction
in call sites is a bug — the type field gets silently ignored, and
operators who switch an agent's executor via the UI see no change in
runtime behavior (prod-incident risk).

Resolution order for an agent's executor config:
  1. Tenant-scoped ExecutorConfig row with ``is_default=True``.
  2. Agent's own ``executor_config`` dict — still only model/base_url
     are applied; the structural choice of which executor class comes
     from ``agent.executor_type``.

A full unified interface for mixing LiteLLM-style and CLI-style
executors is still in progress (see #39 in issues-to-fix.md). This
module ships the minimum required for a claude_code cross-check.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.executor.claude_code_adapter import ClaudeCodeLLMAdapter
from backend.src.core.executor.litellm_executor import LiteLLMExecutor
from backend.src.models import Agent
from backend.src.models.executor_config import ExecutorConfig

logger = structlog.get_logger(__name__)


async def _resolve_tenant_default(
    tenant_id, db: AsyncSession,
) -> ExecutorConfig | None:
    result = await db.execute(
        select(ExecutorConfig).where(
            ExecutorConfig.tenant_id == tenant_id,
            ExecutorConfig.is_default.is_(True),
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def get_executor_for_agent(
    agent: Agent,
    db: AsyncSession,
    *,
    workspace_dir: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Pick the right executor + model config for one agent turn.

    Returns a ``(executor, model_config)`` pair:
      - ``executor`` has an ``execute(messages, tools, tool_handler, …)``
        coroutine compatible with the LiteLLM shape (adapters wrap
        non-LiteLLM executors for parity).
      - ``model_config`` carries ``{model, api_key, base_url}`` — the
        fields LiteLLM needs; ignored by CLI-style adapters.

    Dispatch order:
      ``agent.executor_type``  —  ``"claude_code"`` → ClaudeCodeLLMAdapter,
      everything else (including empty / unknown) → LiteLLMExecutor with
      the tenant default config.
    """
    exec_type = (agent.executor_type or "").strip().lower()
    tenant_default = await _resolve_tenant_default(agent.tenant_id, db)

    # Model config comes from the tenant default (generic_llm entry)
    # regardless of which executor wins — claude_code ignores it but
    # LiteLLM needs it. Fall back to safe empty values if missing.
    base_cfg: dict[str, Any] = {}
    if tenant_default is not None:
        raw = tenant_default.config or {}
        base_cfg = dict(raw)
    model_cfg: dict[str, Any] = {
        "model": base_cfg.get("model", ""),
        "api_key": base_cfg.get("api_key", "unused"),
        "base_url": base_cfg.get("base_url"),
    }

    if exec_type == "claude_code":
        logger.info(
            "executor_factory_claude_code",
            agent=agent.name, tenant_id=str(agent.tenant_id),
        )
        return ClaudeCodeLLMAdapter(workspace_dir=workspace_dir), model_cfg

    # Default: LiteLLM. Covers "", "generic_llm", and anything unknown.
    if exec_type and exec_type not in {"generic_llm", ""}:
        logger.warning(
            "executor_factory_unknown_type_fallback_litellm",
            agent=agent.name, executor_type=exec_type,
        )
    return LiteLLMExecutor(), model_cfg
