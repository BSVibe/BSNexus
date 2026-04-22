"""LocalStorage — filesystem backend for dev/CI without containers.

Content is laid out under ``base_dir / key``. Hash is computed on write.
``presign`` returns a ``file://`` URI (local-only; not for production).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

from backend.src.core.storage.deliverable_storage import ContentRef
from backend.src.models import StorageBackend


async def _iter_to_bytes(content: AsyncIterator[bytes] | bytes) -> bytes:
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    chunks: list[bytes] = []
    async for chunk in content:
        chunks.append(chunk)
    return b"".join(chunks)


class LocalStorage:
    backend = StorageBackend.object

    def __init__(self, base_dir: str | Path):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        safe_key = key.lstrip("/")
        return self._base / safe_key

    async def put(
        self,
        key: str,
        content: AsyncIterator[bytes] | bytes,
        *,
        metadata: dict | None = None,
    ) -> ContentRef:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = await _iter_to_bytes(content)
        path.write_bytes(data)
        etag = hashlib.sha256(data).hexdigest()
        return ContentRef(
            backend=StorageBackend.object,
            data={"bucket": "local", "key": key, "etag": etag, "path": str(path)},
        )

    async def get(self, ref: ContentRef) -> AsyncIterator[bytes]:
        path = Path(ref.data.get("path") or self._path_for(ref.data["key"]))
        data = path.read_bytes()

        async def _gen() -> AsyncIterator[bytes]:
            yield data

        return _gen()

    async def presign(self, ref: ContentRef, ttl_s: int = 3600) -> str:
        path = ref.data.get("path") or str(self._path_for(ref.data["key"]))
        return f"file://{path}"

    async def delete(self, ref: ContentRef) -> None:
        path = Path(ref.data.get("path") or self._path_for(ref.data["key"]))
        if path.exists():
            path.unlink()
