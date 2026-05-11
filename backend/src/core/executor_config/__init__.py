"""``backend.src.core.executor_config`` — per-tenant LLM dispatch resolver.

Loads the ``executor_configs`` row for a tenant, decrypts
``api_key_encrypted`` via ``EncryptionManager``, and returns either a
``BSGatewayClient`` or a ``DirectLLMAdapter``. Both implement the
same ``execute()`` contract so the caller (G6.3 RunAttempt executor)
doesn't branch on kind.

This is the indirection that the G7.5b admin surface (the LLM Dispatch
tab in Settings) populates and the runtime consumes.
"""

from backend.src.core.executor_config.resolver import (
    ExecutorClient,
    ExecutorConfigError,
    resolve_executor,
)

__all__ = ["ExecutorClient", "ExecutorConfigError", "resolve_executor"]
