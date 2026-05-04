"""Executor configuration schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# Valid executor types — taxonomy collapsed 2026-05-04 to two top-level
# kinds, distinguished by *infra dependency* not by *capability*. Both
# carry full MCP / Decisions / artifact UX:
#
#   bsgateway   — route through BSGateway (BSVibe infra). Cost-aware
#                 routing, CLI agent pool, multi-tenant. cfg.model
#                 carries the model string BSGateway routes
#                 (``claude_code``, ``openai/gpt-4o``, ...).
#   generic_llm — direct LLM call from BSNexus (BSVibe optional).
#                 cfg.model is a litellm-style identifier
#                 (``anthropic/claude-3-7-sonnet``, ``ollama/llama3``,
#                 ``openai/gpt-4o``); MCP wired client-side via the
#                 tool-loop in ``core.llm.direct_client``.
#
# Legacy values (``claude_code`` / ``codex`` / ``opencode`` / ``worker``)
# from the pre-2026-05-04 taxonomy are auto-lifted by the alembic
# migration ``2026_05_04_collapse_executor_types`` — old rows become
# ``executor_type=bsgateway`` with the original value moved to
# ``config.model``.
EXECUTOR_TYPES = {"bsgateway", "generic_llm"}


# Keys the API never returns in the ``config`` response payload, even
# if a legacy row still carries them. Rotation hygiene — see the
# 2026-05-04 ``executor_config_api_key_encrypted`` migration.
SENSITIVE_CONFIG_KEYS: frozenset[str] = frozenset({"bsgateway_api_key", "api_key"})


def _redact_config(config: dict) -> dict:
    """Strip sensitive keys from the response-side ``config`` dict."""
    return {k: v for k, v in (config or {}).items() if k not in SENSITIVE_CONFIG_KEYS}


class ExecutorConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    executor_type: str
    config: dict = Field(default_factory=dict)
    description: Optional[str] = None
    is_selected: bool = False
    # Optional plaintext API key — encrypted server-side and stored on
    # ``executor_configs.api_key_encrypted``. May also be supplied
    # inside ``config["bsgateway_api_key"]`` for backwards compat with
    # pre-2026-05-04 callers; both paths land in the encrypted column
    # and the plaintext is dropped from the JSON config.
    api_key: Optional[str] = None


class ExecutorConfigUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    config: Optional[dict] = None
    description: Optional[str] = None
    is_selected: Optional[bool] = None
    api_key: Optional[str] = None


class ExecutorConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    executor_type: str
    config: dict = Field(default_factory=dict)
    description: Optional[str] = None
    is_selected: bool = False
    has_api_key: bool = False
    created_at: datetime
    updated_at: datetime
