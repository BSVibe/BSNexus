"""``resolve_executor(tenant_id, session)`` — load per-tenant LLM
dispatch config and return the right client.

Two-path LLM dispatch (CLAUDE.md MUST rule):
  - ``ExecutorKind.bsgateway`` → ``BSGatewayClient``
  - ``ExecutorKind.llm_api``   → ``DirectLLMAdapter``

Both expose the same ``execute()`` contract, so the caller treats the
return value as a single ``ExecutorClient`` type alias.
"""

from __future__ import annotations

import uuid
from typing import Union

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings as app_settings
from backend.src.core.bsgateway.client import BSGatewayClient
from backend.src.core.encryption import EncryptionManager
from backend.src.core.llm import DirectLLMAdapter
from backend.src.models.executor_config import ExecutorConfig, ExecutorKind

logger = structlog.get_logger(__name__)

ExecutorClient = Union[BSGatewayClient, DirectLLMAdapter]


class ExecutorConfigError(Exception):
    """Raised when a per-tenant ExecutorConfig row exists but cannot
    produce a usable client — typically because the encrypted api_key
    fails to decrypt (rotated encryption key, hand-edited DB).

    The caller treats this as a config error: block the run instead of
    dispatching to BSGateway with nonsense credentials.
    """


async def resolve_executor(
    *,
    tenant_id: uuid.UUID,
    session: AsyncSession,
) -> ExecutorClient | None:
    """Return the per-tenant client, or ``None`` if no config row
    exists. Tenant-scoped: another tenant's row never leaks.
    """
    stmt = select(ExecutorConfig).where(ExecutorConfig.tenant_id == tenant_id)
    config = (await session.execute(stmt)).scalar_one_or_none()
    if config is None:
        return None

    api_key = _decrypt_api_key(config)

    if config.kind == ExecutorKind.bsgateway:
        # G7.5e — BSGateway SaaS defaults to SSO auth; the per-tenant
        # registration token is optional. Pass empty string when not
        # configured so the client still constructs and the SSO cookie
        # carries auth on the wire.
        base_url = config.base_url or ""
        return BSGatewayClient(base_url=base_url, api_key=api_key)

    if config.kind == ExecutorKind.llm_api:
        return DirectLLMAdapter(base_url=config.base_url, api_key=api_key)

    raise ExecutorConfigError(f"Unknown executor kind: {config.kind!r}")


def _decrypt_api_key(config: ExecutorConfig) -> str:
    if not config.api_key_encrypted:
        return ""
    try:
        return EncryptionManager(app_settings.encryption_key).decrypt_value(config.api_key_encrypted)
    except Exception as exc:
        logger.error(
            "executor_config_decrypt_failed",
            tenant_id=str(config.tenant_id),
            kind=config.kind.value,
        )
        raise ExecutorConfigError(f"Failed to decrypt executor api_key for tenant {config.tenant_id}") from exc
