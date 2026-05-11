"""Tests for ``core.executor_config.resolve_executor`` (G6.2 — Piece 5).

Loads the per-tenant ``ExecutorConfig`` row, decrypts
``api_key_encrypted``, returns either a ``BSGatewayClient`` or a
``DirectLLMAdapter``. Both implement the same ``execute()`` contract so
the G6.3 RunAttempt executor can call ``client.execute(...)`` without
caring about the path.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.config import settings as app_settings
from backend.src.core.encryption import EncryptionManager
from backend.src.models.executor_config import ExecutorConfig, ExecutorKind


async def _seed(db_session, tenant_id: uuid.UUID, **kwargs) -> ExecutorConfig:
    row = ExecutorConfig(tenant_id=tenant_id, **kwargs)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_resolver_returns_none_when_no_executor_configured(db_session, mock_tenant_id, seeded_tenant):
    """The tenant hasn't saved an LLM dispatch config yet. The caller
    (G6.3 RunAttempt executor) decides whether to block the run or
    fall back; the resolver itself just signals "nothing here"."""
    from backend.src.core.executor_config import resolve_executor

    client = await resolve_executor(tenant_id=mock_tenant_id, session=db_session)
    assert client is None


@pytest.mark.asyncio
async def test_resolver_returns_bsgateway_client_for_bsgateway_kind(db_session, mock_tenant_id, seeded_tenant):
    """A bsgateway-kind config produces a ``BSGatewayClient`` with the
    decrypted registration token + the saved base_url."""
    from backend.src.core.bsgateway.client import BSGatewayClient
    from backend.src.core.executor_config import resolve_executor

    enc = EncryptionManager(app_settings.encryption_key)
    await _seed(
        db_session,
        mock_tenant_id,
        kind=ExecutorKind.bsgateway,
        base_url="https://gateway.bsvibe.dev",
        api_key_encrypted=enc.encrypt_value("registration-token-abc"),
    )

    client = await resolve_executor(tenant_id=mock_tenant_id, session=db_session)
    assert isinstance(client, BSGatewayClient)
    # No public accessor for the decrypted token — assert via the
    # private attr that the wire-call sites use.
    assert client._base_url == "https://gateway.bsvibe.dev"  # noqa: SLF001
    assert client._api_key == "registration-token-abc"  # noqa: SLF001


@pytest.mark.asyncio
async def test_resolver_returns_direct_llm_adapter_for_llm_api_kind(db_session, mock_tenant_id, seeded_tenant):
    """An llm_api-kind config produces a ``DirectLLMAdapter`` with the
    decrypted provider key + the saved base_url + model."""
    from backend.src.core.executor_config import resolve_executor
    from backend.src.core.llm import DirectLLMAdapter

    enc = EncryptionManager(app_settings.encryption_key)
    await _seed(
        db_session,
        mock_tenant_id,
        kind=ExecutorKind.llm_api,
        base_url="http://host.docker.internal:11434",
        model="ollama_chat/qwen3-coder:30b",
        api_key_encrypted=enc.encrypt_value("sk-secret"),
    )

    client = await resolve_executor(tenant_id=mock_tenant_id, session=db_session)
    assert isinstance(client, DirectLLMAdapter)
    assert client._base_url == "http://host.docker.internal:11434"  # noqa: SLF001
    assert client._api_key == "sk-secret"  # noqa: SLF001


@pytest.mark.asyncio
async def test_resolver_handles_missing_api_key_for_bsgateway_sso_path(db_session, mock_tenant_id, seeded_tenant):
    """G7.5e — BSGateway SaaS is the default and ``api_key`` is
    optional because SSO covers auth. The resolver must produce a
    client with an empty token (the wire side will rely on SSO cookies
    or service-JWT minting); blowing up on a missing key would lock
    every SaaS user out."""
    from backend.src.core.bsgateway.client import BSGatewayClient
    from backend.src.core.executor_config import resolve_executor

    await _seed(
        db_session,
        mock_tenant_id,
        kind=ExecutorKind.bsgateway,
        base_url="https://gateway.bsvibe.dev",
        api_key_encrypted=None,
    )

    client = await resolve_executor(tenant_id=mock_tenant_id, session=db_session)
    assert isinstance(client, BSGatewayClient)
    assert client._api_key == ""  # noqa: SLF001


@pytest.mark.asyncio
async def test_resolver_is_tenant_scoped(db_session, mock_tenant_id, seeded_tenant):
    """Another tenant's config must not leak — same guardrail as the
    /executor-config GET route."""
    from backend.src.core.executor_config import resolve_executor
    from backend.src.models import Tenant

    other_tenant_id = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tenant_id,
            name="Other",
            slug=f"other-{other_tenant_id.hex[:8]}",
            owner_user_id="other-user",
        )
    )
    await db_session.flush()
    await _seed(
        db_session,
        other_tenant_id,
        kind=ExecutorKind.bsgateway,
        base_url="https://leak.example",
        api_key_encrypted=None,
    )

    # ``mock_tenant_id`` has no config of its own → None, even though
    # ``other_tenant_id`` does.
    assert await resolve_executor(tenant_id=mock_tenant_id, session=db_session) is None


@pytest.mark.asyncio
async def test_resolver_raises_on_corrupt_encrypted_blob(db_session, mock_tenant_id, seeded_tenant):
    """A decryption failure (rotated key, hand-edited DB) must surface
    as ``ExecutorConfigError`` rather than silently producing a client
    with a junk token. The caller (G6.3) treats it as a config error
    and blocks the run instead of dispatching to BSGateway with
    nonsense credentials."""
    from backend.src.core.executor_config import ExecutorConfigError, resolve_executor

    await _seed(
        db_session,
        mock_tenant_id,
        kind=ExecutorKind.llm_api,
        base_url="http://localhost:11434",
        model="ollama_chat/qwen3-coder:30b",
        api_key_encrypted="not-a-valid-encrypted-blob",
    )

    with pytest.raises(ExecutorConfigError):
        await resolve_executor(tenant_id=mock_tenant_id, session=db_session)
