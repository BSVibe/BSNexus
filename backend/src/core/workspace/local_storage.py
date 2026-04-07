"""Local filesystem storage backend."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from backend.src.core.workspace.storage_backend import FileInfo, StorageBackend


class LocalStorageBackend(StorageBackend):
    """Store project workspaces on the local filesystem.

    Layout: ``{base_dir}/{workspace_id}/``
    """

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)

    # ── helpers ───────────────────────────────────────────────────

    def _root(self, workspace_id: str) -> Path:
        # Prevent path traversal
        safe_id = Path(workspace_id).name
        return self._base / safe_id

    def _resolve(self, workspace_id: str, path: str) -> Path:
        root = self._root(workspace_id)
        resolved = (root / path).resolve()
        if not str(resolved).startswith(str(root.resolve())):
            raise PermissionError(f"Path traversal detected: {path}")
        return resolved

    # ── interface ─────────────────────────────────────────────────

    async def init_workspace(self, workspace_id: str) -> str:
        root = self._root(workspace_id)
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        return str(root)

    async def delete_workspace(self, workspace_id: str) -> None:
        root = self._root(workspace_id)
        if root.exists():
            await asyncio.to_thread(shutil.rmtree, str(root), ignore_errors=True)

    async def read_file(self, workspace_id: str, path: str) -> bytes:
        target = self._resolve(workspace_id, path)
        if not target.is_file():
            raise FileNotFoundError(f"Not found: {path}")

        def _read() -> bytes:
            return target.read_bytes()

        return await asyncio.to_thread(_read)

    async def write_file(self, workspace_id: str, path: str, content: bytes) -> None:
        target = self._resolve(workspace_id, path)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)

        def _write() -> None:
            target.write_bytes(content)

        await asyncio.to_thread(_write)

    async def delete_file(self, workspace_id: str, path: str) -> None:
        target = self._resolve(workspace_id, path)
        if target.is_file():
            await asyncio.to_thread(target.unlink)

    async def list_files(
        self, workspace_id: str, prefix: str = "", *, recursive: bool = False
    ) -> list[FileInfo]:
        root = self._root(workspace_id)
        target = self._resolve(workspace_id, prefix) if prefix else root

        if not target.is_dir():
            return []

        def _scan() -> list[FileInfo]:
            items: list[FileInfo] = []
            if recursive:
                for dirpath, dirnames, filenames in os.walk(target):
                    rel_dir = os.path.relpath(dirpath, root)
                    # Skip hidden dirs (.git, etc.)
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for d in sorted(dirnames):
                        full = os.path.join(dirpath, d)
                        rel = os.path.join(rel_dir, d) if rel_dir != "." else d
                        items.append(FileInfo(path=rel, name=d, is_dir=True))
                    for f in sorted(filenames):
                        if f.startswith("."):
                            continue
                        full = os.path.join(dirpath, f)
                        rel = os.path.join(rel_dir, f) if rel_dir != "." else f
                        stat = os.stat(full)
                        items.append(FileInfo(
                            path=rel,
                            name=f,
                            is_dir=False,
                            size=stat.st_size,
                            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        ))
            else:
                with os.scandir(target) as it:
                    for entry in sorted(it, key=lambda e: (not e.is_dir(), e.name)):
                        if entry.name.startswith("."):
                            continue
                        rel = os.path.relpath(entry.path, root)
                        if entry.is_dir():
                            items.append(FileInfo(path=rel, name=entry.name, is_dir=True))
                        else:
                            stat = entry.stat()
                            items.append(FileInfo(
                                path=rel,
                                name=entry.name,
                                is_dir=False,
                                size=stat.st_size,
                                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                            ))
            return items

        return await asyncio.to_thread(_scan)

    async def exists(self, workspace_id: str, path: str) -> bool:
        target = self._resolve(workspace_id, path)
        return await asyncio.to_thread(target.exists)

    def get_workspace_path(self, workspace_id: str) -> str:
        return str(self._root(workspace_id))
