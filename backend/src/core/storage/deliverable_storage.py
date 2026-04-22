"""DeliverableStorage Protocol + shared types + factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from backend.src.models import DeliverableType, StorageBackend


@dataclass(frozen=True)
class ContentRef:
    """Persisted reference to a single version's bytes.

    Shape varies by backend:
    - ``git``: ``{"repo": ..., "commit": ..., "path": ...}``
    - ``object``: ``{"bucket": ..., "key": ..., "etag": ...}``
    - ``url``: ``{"url": ...}``

    Serialized into ``deliverable_versions.content_ref`` (JSON).
    """

    backend: StorageBackend
    data: dict

    def to_dict(self) -> dict:
        return {"backend": self.backend.value, **self.data}

    @classmethod
    def from_dict(cls, raw: dict) -> "ContentRef":
        backend_value = raw.get("backend", "object")
        data = {k: v for k, v in raw.items() if k != "backend"}
        return cls(backend=StorageBackend(backend_value), data=data)


class DeliverableStorage(Protocol):
    """Async API for writing and reading deliverable versions."""

    backend: StorageBackend

    async def put(
        self,
        key: str,
        content: AsyncIterator[bytes] | bytes,
        *,
        metadata: dict | None = None,
    ) -> ContentRef: ...

    async def get(self, ref: ContentRef) -> AsyncIterator[bytes]: ...

    async def presign(self, ref: ContentRef, ttl_s: int = 3600) -> str: ...

    async def delete(self, ref: ContentRef) -> None: ...


def storage_for_type(
    deliverable_type: DeliverableType,
    *,
    object_storage: DeliverableStorage,
    git_storage: DeliverableStorage,
) -> DeliverableStorage:
    """Dispatch by deliverable type.

    Code → git. Everything else → object.
    """
    if deliverable_type == DeliverableType.code:
        return git_storage
    return object_storage
