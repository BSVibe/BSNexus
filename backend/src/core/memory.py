"""Agent long-term memory providers.

Two backends ship today:

- ``LocalMemoryProvider`` (default): reads and writes the
  ``agent_memories`` table in the local database.
- ``BSageMemoryProvider``: thin REST client for an external BSage
  knowledge service. Configured per-tenant — when a tenant has BSage
  credentials in its settings, ``MemoryProviderFactory`` returns the
  BSage provider instead of the local one.

Both implement the same ``MemoryProvider`` Protocol so the rest of
the codebase doesn't care which backend is configured.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import AgentMemory


@dataclass
class MemoryRecord:
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID | None
    category: str
    title: str
    content: str
    metadata: dict | None = None


class MemoryProvider(Protocol):
    """Async interface every memory backend must satisfy."""

    async def remember(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        *,
        category: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> MemoryRecord: ...

    async def recall(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        *,
        category: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]: ...

    async def forget(self, memory_id: uuid.UUID) -> bool: ...


class LocalMemoryProvider:
    """Default provider — persists memories in the project DB."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def remember(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        *,
        category: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> MemoryRecord:
        row = AgentMemory(
            project_id=project_id,
            agent_id=agent_id,
            category=category,
            title=title,
            content=content,
            extra_metadata=metadata,
        )
        self._db.add(row)
        await self._db.flush()
        return _to_record(row)

    async def recall(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        *,
        category: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        query = select(AgentMemory).where(AgentMemory.project_id == project_id)
        if agent_id is not None:
            query = query.where(AgentMemory.agent_id == agent_id)
        if category is not None:
            query = query.where(AgentMemory.category == category)
        query = query.order_by(AgentMemory.created_at.desc()).limit(limit)
        result = await self._db.execute(query)
        return [_to_record(row) for row in result.scalars().all()]

    async def forget(self, memory_id: uuid.UUID) -> bool:
        row = await self._db.get(AgentMemory, memory_id)
        if row is None:
            return False
        await self._db.delete(row)
        return True


def _to_record(row: AgentMemory) -> MemoryRecord:
    return MemoryRecord(
        id=row.id,
        project_id=row.project_id,
        agent_id=row.agent_id,
        category=row.category,
        title=row.title,
        content=row.content,
        metadata=row.extra_metadata,
    )


# ── BSage provider ──────────────────────────────────────────────────


class BSageMemoryProvider:
    """Thin REST client for an external BSage knowledge service.

    Activated when a tenant has BSage credentials in its settings.
    The endpoint shape is intentionally simple so any conformant
    implementation works:

      POST   {base_url}/memories                         body: MemoryRecord JSON
      GET    {base_url}/memories?project_id=&agent_id=   list
      DELETE {base_url}/memories/{id}

    Auth: every request carries ``Authorization: Bearer {api_key}``.
    """

    def __init__(self, base_url: str, api_key: str, http_client=None) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._http = http_client  # injected in tests

    async def _client(self):
        if self._http is not None:
            return self._http
        import httpx

        self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def remember(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        *,
        category: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> MemoryRecord:
        body = {
            "project_id": str(project_id),
            "agent_id": str(agent_id) if agent_id else None,
            "category": category,
            "title": title,
            "content": content,
            "metadata": metadata,
        }
        client = await self._client()
        resp = await client.post(f"{self._base}/memories", json=body, headers=self._headers())
        resp.raise_for_status()
        return _record_from_bsage(resp.json())

    async def recall(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        *,
        category: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        params: dict[str, str] = {"project_id": str(project_id), "limit": str(limit)}
        if agent_id is not None:
            params["agent_id"] = str(agent_id)
        if category is not None:
            params["category"] = category
        client = await self._client()
        resp = await client.get(f"{self._base}/memories", params=params, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "items" in data:
            data = data["items"]
        return [_record_from_bsage(item) for item in data]

    async def forget(self, memory_id: uuid.UUID) -> bool:
        client = await self._client()
        resp = await client.delete(f"{self._base}/memories/{memory_id}", headers=self._headers())
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True


def _record_from_bsage(payload: dict) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.UUID(payload["id"]),
        project_id=uuid.UUID(payload["project_id"]),
        agent_id=uuid.UUID(payload["agent_id"]) if payload.get("agent_id") else None,
        category=payload.get("category", "decision"),
        title=payload.get("title", ""),
        content=payload.get("content", ""),
        metadata=payload.get("metadata"),
    )


# ── Factory ─────────────────────────────────────────────────────────


def make_memory_provider(db: AsyncSession, tenant_settings: dict | None = None) -> MemoryProvider:
    """Choose a memory backend based on the tenant's settings.

    Tenants opt into BSage by storing ``bsage_base_url`` and
    ``bsage_api_key`` in their settings JSON. Without those, the
    LocalMemoryProvider is returned and everything stays in the local DB.
    """
    if tenant_settings:
        base = tenant_settings.get("bsage_base_url")
        key = tenant_settings.get("bsage_api_key")
        if base and key:
            return BSageMemoryProvider(base, key)
    return LocalMemoryProvider(db)
