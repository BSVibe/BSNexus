"""KnowledgeProvider protocol and implementations.

Self-contained module — no cross-provider imports.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

logger = structlog.get_logger(__name__)


@runtime_checkable
class KnowledgeProvider(Protocol):
    """Knowledge base interface using structural subtyping."""

    async def get_sot(self, project_id: str) -> dict[str, Any]: ...

    async def store_result(self, task_id: str, result: dict[str, Any]) -> None: ...

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...


class BSageProvider:
    """Routes knowledge calls through BSage HTTP API."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def get_sot(self, project_id: str) -> dict[str, Any]:
        """Fetch source-of-truth for a project from BSage API."""
        logger.info("bsage_get_sot", project_id=project_id)

        resp = await self._client.get(
            f"{self._base_url}/api/knowledge/sot/{project_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()

        return resp.json()

    async def store_result(self, task_id: str, result: dict[str, Any]) -> None:
        """Store a task result in BSage API."""
        body: dict[str, Any] = {
            "task_id": task_id,
            "result": result,
        }

        logger.info("bsage_store_result", task_id=task_id)

        resp = await self._client.post(
            f"{self._base_url}/api/knowledge/results/{task_id}",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search the knowledge base via BSage API."""
        body: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }

        logger.info("bsage_search", query=query, limit=limit)

        resp = await self._client.post(
            f"{self._base_url}/api/knowledge/search",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()

        return resp.json()["results"]


class LocalMarkdownProvider:
    """Local filesystem fallback — reads/writes markdown files in a knowledge directory."""

    def __init__(self, knowledge_dir: str | Path) -> None:
        self._knowledge_dir = Path(knowledge_dir)

    async def get_sot(self, project_id: str) -> dict[str, Any]:
        """Read source-of-truth from a local markdown file."""
        sot_file = self._knowledge_dir / "sot" / f"{project_id}.md"

        if not sot_file.exists():
            logger.info("local_knowledge_sot_not_found", project_id=project_id)
            return {}

        content = await asyncio.to_thread(sot_file.read_text, encoding="utf-8")
        logger.info("local_knowledge_get_sot", project_id=project_id)

        return {"project_id": project_id, "content": content}

    async def store_result(self, task_id: str, result: dict[str, Any]) -> None:
        """Write task result to a local markdown file."""
        results_dir = self._knowledge_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        result_file = results_dir / f"{task_id}.md"
        content = f"# Task Result: {task_id}\n\n```json\n{json.dumps(result, indent=2)}\n```\n"
        await asyncio.to_thread(result_file.write_text, content, encoding="utf-8")

        logger.info("local_knowledge_store_result", task_id=task_id)

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search local markdown files for content matching the query."""
        docs_dir = self._knowledge_dir / "docs"

        if not docs_dir.exists():
            return []

        results: list[dict[str, Any]] = []
        query_lower = query.lower()

        for md_file in sorted(docs_dir.glob("*.md")):
            content = await asyncio.to_thread(md_file.read_text, encoding="utf-8")
            if query_lower in content.lower():
                results.append({
                    "file": md_file.name,
                    "content": content,
                })
                if len(results) >= limit:
                    break

        logger.info("local_knowledge_search", query=query, results_count=len(results))
        return results
