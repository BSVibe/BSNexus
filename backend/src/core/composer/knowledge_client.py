"""KnowledgeClient — wrapper around BSage knowledge primitives.

BSage is the project's long-term knowledge graph. BSNexus both reads
from it (to enrich prompts with prior context) and writes back to it
(to index run outputs so future projects can find them).

Read endpoints:
- ``GET  /api/knowledge/search?q=…&limit=…``
- ``GET  /api/vault/file?path=...``
- ``GET  /api/vault/backlinks?path=...``

Write endpoints:
- ``POST /api/knowledge/entries``   — generic knowledge note
- ``POST /api/knowledge/decisions`` — structured decision record

``NoopKnowledgeClient`` is the fallback when BSage is disabled or
unreachable for the tenant — search/fetch/backlinks return empty,
writes return None. PromptAssembler and publish_run_output still
produce a valid (degraded) result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx
import structlog

from backend.src.core.integrations.config import ProviderConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class KnowledgeFragment:
    """One retrieved piece of knowledge from BSage."""

    path: str
    title: str
    excerpt: str
    score: float
    extra: dict = field(default_factory=dict)

    def to_ref(self) -> dict:
        """Serialize for composition_snapshots.context_doc_refs."""
        return {
            "path": self.path,
            "title": self.title,
            "score": self.score,
            "excerpt_hash": _hash_text(self.excerpt),
        }


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class KnowledgeEntryRef:
    """Reference to a knowledge entry that was indexed in BSage."""

    id: str
    path: str


class KnowledgeClient(Protocol):
    """Protocol for knowledge retrieval + indexing backends.

    Implementations must NOT raise on transient failures — return empty
    or None so the calling code can always complete its main job
    (composition / deliverable creation / decision resolution) even if
    BSage is down.
    """

    async def search(
        self, intent: str, *, top_k: int = 10
    ) -> list[KnowledgeFragment]: ...

    async def fetch(self, path: str) -> str | None: ...

    async def backlinks(self, path: str) -> list[str]: ...

    async def index(
        self,
        *,
        title: str,
        content: str,
        note_type: str = "idea",
        tags: list[str] | None = None,
        links: list[str] | None = None,
        source: str = "bsnexus",
        metadata: dict | None = None,
    ) -> KnowledgeEntryRef | None: ...

    async def record_decision(
        self,
        *,
        title: str,
        decision: str,
        reasoning: str,
        alternatives: list[str] | None = None,
        context: str = "",
        tags: list[str] | None = None,
        source: str = "bsnexus",
    ) -> KnowledgeEntryRef | None: ...


class NoopKnowledgeClient:
    """Fallback when BSage is disabled or unreachable.

    All methods are no-ops. The resulting composition gets
    ``source="local"`` so the Inside panel can surface degraded mode.
    """

    async def search(
        self, intent: str, *, top_k: int = 10
    ) -> list[KnowledgeFragment]:
        return []

    async def fetch(self, path: str) -> str | None:
        return None

    async def backlinks(self, path: str) -> list[str]:
        return []

    async def index(
        self,
        *,
        title: str,
        content: str,
        note_type: str = "idea",
        tags: list[str] | None = None,
        links: list[str] | None = None,
        source: str = "bsnexus",
        metadata: dict | None = None,
    ) -> KnowledgeEntryRef | None:
        return None

    async def record_decision(
        self,
        *,
        title: str,
        decision: str,
        reasoning: str,
        alternatives: list[str] | None = None,
        context: str = "",
        tags: list[str] | None = None,
        source: str = "bsnexus",
    ) -> KnowledgeEntryRef | None:
        return None


class BSageKnowledgeClient:
    """HTTP client against BSage's existing endpoints.

    Timeout + circuit-break: on repeated failures fall back silently to
    empty results. Never raise out of the Protocol contract.
    """

    def __init__(self, base_url: str, api_key: str | None, *, timeout_s: float = 3.0):
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        # Explicit service UA — Cloudflare's Bot Fight Mode on the
        # *.bsvibe.dev frontends 403s httpx's default ``python-httpx/x.y``
        # as a bot. A named service identifier is treated as a normal
        # backend-to-backend call.
        self._headers: dict[str, str] = {
            "User-Agent": "BSNexus/0.2 (+https://nexus.bsvibe.dev)",
        }
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    async def search(
        self, intent: str, *, top_k: int = 10
    ) -> list[KnowledgeFragment]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.get(
                    f"{self._base_url}/api/knowledge/search",
                    params={"q": intent, "limit": top_k},
                    headers=self._headers,
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.warning("bsage_search_failed", error=str(exc))
            return []

        # BSage's SearchResult fields: title, path, preview, score, tags.
        # Earlier/alternate payloads may use content/excerpt instead of preview.
        known_keys = {"path", "title", "preview", "excerpt", "content", "score"}
        return [
            KnowledgeFragment(
                path=hit.get("path", ""),
                title=hit.get("title", hit.get("path", "")),
                excerpt=hit.get("preview")
                or hit.get("excerpt")
                or hit.get("content", ""),
                score=float(hit.get("score", 0.0)),
                extra={k: v for k, v in hit.items() if k not in known_keys},
            )
            for hit in payload.get("results", [])
        ]

    async def fetch(self, path: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.get(
                    f"{self._base_url}/api/vault/file",
                    params={"path": path},
                    headers=self._headers,
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json().get("content")
        except Exception as exc:
            logger.warning("bsage_fetch_failed", path=path, error=str(exc))
            return None

    async def backlinks(self, path: str) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.get(
                    f"{self._base_url}/api/vault/backlinks",
                    params={"path": path},
                    headers=self._headers,
                )
                resp.raise_for_status()
                return list(resp.json().get("backlinks", []))
        except Exception as exc:
            logger.warning("bsage_backlinks_failed", path=path, error=str(exc))
            return []

    async def index(
        self,
        *,
        title: str,
        content: str,
        note_type: str = "idea",
        tags: list[str] | None = None,
        links: list[str] | None = None,
        source: str = "bsnexus",
        metadata: dict | None = None,
    ) -> KnowledgeEntryRef | None:
        body = {
            "title": title,
            "content": content,
            "note_type": note_type,
            "tags": list(tags or []),
            "links": list(links or []),
            "source": source,
            "metadata": dict(metadata or {}),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(
                    f"{self._base_url}/api/knowledge/entries",
                    json=body,
                    headers=self._headers,
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.warning("bsage_index_failed", title=title[:60], error=str(exc))
            return None
        return KnowledgeEntryRef(
            id=str(payload.get("id", "")),
            path=str(payload.get("path", "")),
        )

    async def record_decision(
        self,
        *,
        title: str,
        decision: str,
        reasoning: str,
        alternatives: list[str] | None = None,
        context: str = "",
        tags: list[str] | None = None,
        source: str = "bsnexus",
    ) -> KnowledgeEntryRef | None:
        body = {
            "title": title,
            "decision": decision,
            "reasoning": reasoning,
            "alternatives": list(alternatives or []),
            "context": context,
            "tags": list(tags or []),
            "source": source,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(
                    f"{self._base_url}/api/knowledge/decisions",
                    json=body,
                    headers=self._headers,
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.warning("bsage_decision_failed", title=title[:60], error=str(exc))
            return None
        return KnowledgeEntryRef(
            id=str(payload.get("id", "")),
            path=str(payload.get("path", "")),
        )


def resolve_knowledge_client(cfg: ProviderConfig | None) -> KnowledgeClient:
    """Factory: return BSage client when configured, Noop otherwise."""
    if cfg is not None and cfg.enabled and cfg.base_url:
        return BSageKnowledgeClient(cfg.base_url, cfg.api_key)
    return NoopKnowledgeClient()
