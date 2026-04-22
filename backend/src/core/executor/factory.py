"""Executor factory — single entry point for all LLM calls.

Every LLM invocation must go through this factory so the tenant's
configured executor_type is honored. Direct ``LiteLLMExecutor()`` /
``ClaudeCodeLLMAdapter()`` construction at call sites bypasses this
guarantee.

Post-founder-metaphor: callers pass tenant_id + executor_type (from the
composition snapshot) rather than an Agent object.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.executor.claude_code_adapter import ClaudeCodeLLMAdapter
from backend.src.core.executor.litellm_executor import LiteLLMExecutor
from backend.src.models.executor_config import ExecutorConfig

logger = structlog.get_logger(__name__)


async def _resolve_tenant_default(
    tenant_id: uuid.UUID, db: AsyncSession
) -> ExecutorConfig | None:
    result = await db.execute(
        select(ExecutorConfig)
        .where(
            ExecutorConfig.tenant_id == tenant_id,
            ExecutorConfig.is_default.is_(True),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_executor(
    tenant_id: uuid.UUID,
    executor_type: str | None,
    db: AsyncSession,
    *,
    workspace_dir: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Pick executor + model config for one run.

    Returns ``(executor, model_config)``:
      - ``executor`` has an ``execute(messages, tools, tool_handler, …)``
        coroutine.
      - ``model_config`` = ``{model, api_key, base_url}`` — LiteLLM uses
        them; CLI adapters ignore.

    Dispatch:
      ``executor_type == "claude_code"`` → ClaudeCodeLLMAdapter.
      Everything else → LiteLLMExecutor with tenant default config.
    """
    exec_type = (executor_type or "").strip().lower()
    tenant_default = await _resolve_tenant_default(tenant_id, db)

    base_cfg: dict[str, Any] = {}
    if tenant_default is not None:
        base_cfg = dict(tenant_default.config or {})
    model_cfg: dict[str, Any] = {
        "model": base_cfg.get("model", ""),
        "api_key": base_cfg.get("api_key", "unused"),
        "base_url": base_cfg.get("base_url"),
    }

    if exec_type == "claude_code":
        logger.info("executor_factory_claude_code", tenant_id=str(tenant_id))
        return ClaudeCodeLLMAdapter(workspace_dir=workspace_dir), model_cfg

    if exec_type and exec_type not in {"generic_llm", ""}:
        logger.warning(
            "executor_factory_unknown_type_fallback_litellm",
            tenant_id=str(tenant_id),
            executor_type=exec_type,
        )
    return LiteLLMExecutor(), model_cfg
