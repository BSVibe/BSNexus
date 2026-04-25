"""Shared HTTP client primitives for sibling-service integrations.

``BaseServiceClient`` consolidates the Bearer + UA + 3s-timeout +
fail-soft pattern used by ``core/composer/knowledge_client.py`` (BSage)
and ``core/audit/audit_sink.py`` (BSupervisor) — and the integration
probe in ``api/integrations.py``.

Design (decision #15): ``ServiceClientProtocol`` is a ``@runtime_checkable``
Protocol — structural typing, not ABC inheritance. ``BaseServiceClient``
is a concrete reference implementation; concrete adapters compose it
rather than inherit it.

Auth boundary: the Bearer value comes from an injected ``auth_provider``
callable (sync or async). Phase A: closure returns the tenant's static
``api_key``. Phase 0 P0.7: closure is swapped to a service-JWT minter
without changing adapter code or this client.
"""

from backend.src.core.clients.base import (
    AuthProvider,
    BaseServiceClient,
    ServiceClientProtocol,
)

__all__ = ["AuthProvider", "BaseServiceClient", "ServiceClientProtocol"]
