"""Abstract storage backend — swappable between local FS and S3."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FileInfo:
    """Metadata for a single file or directory."""

    path: str
    name: str
    is_dir: bool
    size: int = 0
    modified_at: datetime | None = None


class StorageBackend(ABC):
    """Abstract file storage interface.

    Implementations:
    - LocalStorageBackend: local filesystem (default)
    - (future) S3StorageBackend: AWS S3 / MinIO
    """

    @abstractmethod
    async def init_workspace(self, workspace_id: str) -> str:
        """Create a workspace directory. Returns the root path."""

    @abstractmethod
    async def delete_workspace(self, workspace_id: str) -> None:
        """Delete a workspace and all its contents."""

    @abstractmethod
    async def read_file(self, workspace_id: str, path: str) -> bytes:
        """Read file contents. Raises FileNotFoundError if missing."""

    @abstractmethod
    async def write_file(self, workspace_id: str, path: str, content: bytes) -> None:
        """Write file contents, creating parent directories as needed."""

    @abstractmethod
    async def delete_file(self, workspace_id: str, path: str) -> None:
        """Delete a single file."""

    @abstractmethod
    async def list_files(
        self, workspace_id: str, prefix: str = "", *, recursive: bool = False
    ) -> list[FileInfo]:
        """List files and directories under prefix."""

    @abstractmethod
    async def exists(self, workspace_id: str, path: str) -> bool:
        """Check if a file or directory exists."""

    @abstractmethod
    def get_workspace_path(self, workspace_id: str) -> str:
        """Return the absolute filesystem path for a workspace (local) or virtual root (S3)."""
