"""``backend.src.core.executor_config`` — per-tenant LLM dispatch resolver.

Public surface:
  - :class:`ExecutorClient` — the Protocol every per-tenant LLM client
    satisfies. Both ``BSGatewayClient`` and ``DirectLLMAdapter``
    structurally implement it; adding a new kind = one new class +
    one resolver branch, no Union widening.
  - :func:`resolve_executor` — load the ``executor_configs`` row for
    a tenant, decrypt the api_key, return the right concrete client
    as an ``ExecutorClient``.
  - :class:`ExecutorConfigError` — raised when the row exists but
    cannot produce a usable client (rotated encryption key, etc.).
"""

from backend.src.core.executor_config.protocol import ExecutorClient
from backend.src.core.executor_config.resolver import (
    ExecutorConfigError,
    resolve_executor,
)

__all__ = ["ExecutorClient", "ExecutorConfigError", "resolve_executor"]
